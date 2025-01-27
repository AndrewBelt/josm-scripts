import sys
import traceback
sys.path.append("/home/vortico/src/py/josm-scripts")
import lib


def main():
    active_layer = lib.get_active_layer()
    osm_layer = lib.get_osm_layer()

    # lib.transfer_selected_nonintersecting_buildings(active_layer, osm_layer)
    # lib.transfer_selected_nonduplicate_addresses(active_layer, osm_layer)
    # lib.merge_selected_addresses_to_buildings(active_layer)
    lib.convert_selected_buildings_to_nodes(active_layer)

    # comment: Review and add buildings and addresses
    # sources: microsoft/BuildingFootprints; esri_USDOT_Tennessee; Esri World Imagery


try:
    main()
except:
    print(traceback.format_exc())
    raise
