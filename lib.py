from java.util import ArrayList, HashMap, HashSet, Collections
from java.util.stream import Collectors

from org.openstreetmap.josm.gui import MainApplication
from org.openstreetmap.josm.data.osm.search import SearchCompiler, SearchSetting
from org.openstreetmap.josm.gui.layer import Layer, OsmDataLayer
from org.openstreetmap.josm.data.osm import DataSet, OsmPrimitive, TagMap, Node, Way, Relation, NodeData
from org.openstreetmap.josm.data import UndoRedoHandler
from org.openstreetmap.josm.command import Command, SequenceCommand, DeleteCommand, AddCommand, AddPrimitivesCommand, ChangePropertyCommand, SelectCommand
from org.openstreetmap.josm.tools import Geometry


def get_active_layer():
    return MainApplication.getLayerManager().getActiveLayer()


def get_osm_layer():
    for layer in MainApplication.getLayerManager().getLayers():
        if isinstance(layer, OsmDataLayer):
            data = layer.getDataSet()
            for data_source in data.getDataSources():
                if data_source.origin.startswith('openstreetmap-cgimap'):
                    return layer
    return None


def print_tags(primitive):
    for tag in primitive.getKeys().entrySet():
        print(tag.getKey() + "=" + tag.getValue())


def search(layer, query):
    matcher = SearchCompiler.compile(query)
    return layer.getDataSet().allNonDeletedPrimitives().stream().filter(matcher).collect(Collectors.toSet())


def search_selected(layer, query):
    matcher = SearchCompiler.compile(query)
    return layer.getDataSet().getSelected().stream().filter(matcher).collect(Collectors.toSet())


def get_intersection_area(way1, way2):
    # Do fast check of intersecting bounding boxes
    bbox1 = way1.getBBox()
    bbox2 = way2.getBBox()
    if not bbox1.intersects(bbox2):
        return 0.0

    # Convert ways to Areas in meters
    area1 = Geometry.getAreaEastNorth(way1)
    area2 = Geometry.getAreaEastNorth(way2)
    result = Geometry.polygonIntersectionResult(area1, area2, 1e-4)

    if result.a == Geometry.PolygonIntersection.OUTSIDE:
        return 0.0
    # TODO Use Shoelace formula instead of bounding box area
    return result.b.getBounds2D().getWidth() * result.b.getBounds2D().getHeight()


def is_intersecting_way_ways(way1, ways2):
    nodes1 = way1.getNodes()
    for way2 in ways2:
        intersection = Geometry.polygonIntersection(nodes1, way2.getNodes())
        if intersection != Geometry.PolygonIntersection.OUTSIDE:
            return True
    return False


def select(layer, primitives, commands=None):
    if commands is None:
        commands = UndoRedoHandler.getInstance()
    commands.add(SelectCommand(layer.getDataSet(), primitives))


def is_matching_tag(p1, p2, key):
    v1 = p1.get(key)
    if not v1:
        return False
    v2 = p2.get(key)
    if not v2:
        return False
    return v1 == v2


def is_matching_address(p1, p2):
    if p1.get('addr:unit') != p2.get('addr:unit'):
        return False
    if not is_matching_tag(p1, p2, 'addr:housenumber'):
        return False
    if not is_matching_tag(p1, p2, 'addr:street'):
        return False
    return True


def transfer_primitives(source_layer, dest_layer, primitives, commands=None):
    if primitives.isEmpty():
        return
    if commands is None:
        commands = UndoRedoHandler.getInstance()

    # Collect all Nodes and Ways, recursively
    node_set = HashSet()
    way_set = HashSet()
    for p in primitives:
        if isinstance(p, Node):
            node_set.add(p)
        elif isinstance(p, Way):
            for node in p.getNodes():
                node_set.add(node)
            way_set.add(p)
        elif isinstance(p, Relation):
            # TODO
            raise Exception("Transferring relations not yet supported")

    # Serialize Nodes and Ways
    node_data = ArrayList([p.save() for p in node_set])
    way_data = ArrayList([p.save() for p in way_set])

    # Build commands in order
    commands2 = ArrayList()
    if not way_set.isEmpty():
        commands2.add(DeleteCommand(source_layer.getDataSet(), way_set))
    # TODO Don't delete Nodes if used by other Ways
    if not node_set.isEmpty():
        commands2.add(DeleteCommand(source_layer.getDataSet(), node_set))
    if not node_data.isEmpty():
        commands2.add(AddPrimitivesCommand(node_data, dest_layer.getDataSet()))
    if not way_data.isEmpty():
        commands2.add(AddPrimitivesCommand(way_data, dest_layer.getDataSet()))

    if commands2.isEmpty():
        return
    commands.add(SequenceCommand("Transfer", commands2))


def delete_primitives(primitives, commands=None):
    if primitives.isEmpty():
        return
    if commands is None:
        commands = UndoRedoHandler.getInstance()
    # Delete nodes in ways if nothing else refers to them
    commands.add(DeleteCommand.delete(primitives, True))


def transfer_selected_nonintersecting_buildings(source_layer, dest_layer):
    source_buildings = search_selected(source_layer, 'type:way closed building=*')
    dest_buildings = search(dest_layer, 'type:way closed building=*')

    nonintersecting_buildings = HashSet([b for b in source_buildings if not is_intersecting_way_ways(b, dest_buildings)])

    if nonintersecting_buildings.isEmpty():
        return
    transfer_primitives(source_layer, dest_layer, nonintersecting_buildings)
    MainApplication.getLayerManager().setActiveLayer(dest_layer)


def transfer_selected_nonduplicate_addresses(source_layer, dest_layer):
    addresses = search_selected(source_layer, 'type:node "addr:housenumber"=*')
    dest_addresses = search(dest_layer, '"addr:housenumber"=*')

    nonduplicate_addresses = HashSet([a for a in addresses if not any(is_matching_address(a, a2) for a2 in dest_addresses)])

    if nonduplicate_addresses.isEmpty():
        return
    transfer_primitives(source_layer, dest_layer, nonduplicate_addresses)
    MainApplication.getLayerManager().setActiveLayer(dest_layer)


def merge_primitives(source, dest, merge_keys=None, commands=None):
    if merge_keys is None:
        merge_keys = []
    if commands is None:
        commands = UndoRedoHandler.getInstance()

    source_tags = source.getKeys()
    dest_tags = dest.getKeys()
    for key in merge_keys:
        source_value = source_tags.get(key)
        dest_value = dest_tags.get(key)
        if dest_value and source_value:
            # Mimic MapWithAI by putting source values before dest values
            value = set(source_value.split(';') + dest_value.split(';'))
            source_tags.put(key, ';'.join(value))

    commands2 = ArrayList()
    commands2.add(ChangePropertyCommand(dest.getDataSet(), Collections.singleton(dest), source_tags))
    commands2.add(DeleteCommand(source.getDataSet(), source))
    commands.add(SequenceCommand("Merge", commands2))


def merge_selected_addresses_to_buildings(layer):
    addresses = search_selected(layer, 'type:node "addr:housenumber"=*')
    unmerged_addresses = HashSet(addresses)
    buildings = search(layer, 'type:way closed building=* -"addr:housenumber"=*')
    building_addresses = HashMap()

    def add_building_address(building, address):
        address_set = building_addresses.get(building) or HashSet()
        address_set.add(address)
        building_addresses.put(building, address_set)
        unmerged_addresses.remove(address)

    # Find addresses contained in buildings
    for building in buildings:
        contained_addresses = Geometry.filterInsidePolygon(unmerged_addresses, building)
        for address in contained_addresses:
            add_building_address(building, address)

    # Find closest building to unmerged address
    for address in list(unmerged_addresses):
        closest_building = None
        closest_dist = float('inf')
        for building in buildings:
            dist = Geometry.getDistanceWayNode(building, address)
            if dist < closest_dist:
                closest_building = building
                closest_dist = dist

        if closest_building and closest_dist < 12:
            add_building_address(closest_building, address)

    commands = ArrayList()
    merged_buildings = HashSet()

    # Merge buildings only if it has exactly 1 address
    for entry in building_addresses.entrySet():
        building = entry.getKey()
        addresses = entry.getValue()
        if addresses.size() == 1:
            address = addresses.iterator().next()
            merge_primitives(address, building, ['source'], commands)
            merged_buildings.add(building)

    if merged_buildings.isEmpty():
        return
    select(layer, merged_buildings, commands)
    UndoRedoHandler.getInstance().add(SequenceCommand("Merge selected addresses to buildings", commands))


def convert_selected_buildings_to_nodes(layer):
    buildings = search_selected(layer, 'type:way closed building=*')
    if buildings.isEmpty():
        return

    nodes = ArrayList()
    for building in buildings:
        node = NodeData()
        centroid = Geometry.getCentroid(building.getNodes())
        node.setEastNorth(centroid)
        node.setKeys(building.getKeys())
        node.remove("building")
        nodes.add(node)

    commands = ArrayList()
    delete_primitives(buildings, commands)
    commands.add(AddPrimitivesCommand(nodes, nodes, layer.getDataSet()))
    UndoRedoHandler.getInstance().add(SequenceCommand("Convert selected buildings to nodes", commands))
