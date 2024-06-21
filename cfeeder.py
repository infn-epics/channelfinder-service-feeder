## Andrea Michelotti

import argparse
import os
import requests
import epics

# List of EPICS fields to extract
epics_fields = [
    "DESC", "ASG", "SCAN", "PINI", "PHAS", "EVNT", "TSE", "TSEL", "DTYP", "DISV", "DISA", "SDIS", "MLOK", "MLIS",
    "FLNK", "VAL", "OUT", "HIGH", "HIHI", "LOW", "LOLO", "HOPR", "LOPR", "DRVH", "DRVL", "EGU", "PREC", "ADEL", "MDEL",
    "ALRM", "STAT", "SEVR", "ACKS", "ACKT", "DISS", "LCNT", "PACT", "PUTF", "RPRO", "ASP", "PPN", "PPNR", "SPVT",
    "RSET", "DSET", "DPVT", "RDES", "LSET", "PRIO", "TPRO", "BKPT", "UDF", "TIME", "FLD"
]

def create_property_if_not_exists(cf_service_url, property_name, username, password):
    # Check if the property already exists
    response = requests.get(f"{cf_service_url}/resources/properties/{property_name}", auth=(username, password))
    
    if response.status_code != 200:
        # Property does not exist, so create it
        property_data = {
            "name": property_name,
            "owner": username  # Assuming the username is the owner
        }
        
        create_response = requests.post(
            f"{cf_service_url}/resources/properties/{property_name}",
            json=property_data,
            auth=(username, password)
        )
        
        if create_response.status_code == 200:
            print(f"Successfully created property: {property_name}")
        else:
            print(f"Failed to create property: {property_name}. Status code: {create_response.status_code}, Response: {create_response.text}")
    

def create_tag_if_not_exists(cf_service_url, tag, username, password):
    # Check if the tag already exists
    response = requests.get(f"{cf_service_url}/resources/tags/{tag}", auth=(username, password))
    
    if response.status_code != 200:
        # Tag does not exist, so create it
        tag_data = {
            "name": tag,
            "owner": username  # Assuming the username is the owner
        }
        
        create_response = requests.post(
            f"{cf_service_url}/resources/tags/{tag}",
            json=tag_data,
            auth=(username, password)
        )
        
        if create_response.status_code == 200:
            print(f"Successfully created tag: {tag}")
        else:
            print(f"Failed to create tag: {tag}. Status code: {create_response.status_code}, Response: {create_response.text}")
   

def create_channel_entry(pv_name, tags, cf_service_url, username, password):
    # Get PV information using pyepics
    pv = epics.PV(pv_name)
    
    # Wait for PV connection
    if not pv.wait_for_connection(timeout=5):
        print(f"Failed to connect to PV: {pv_name}")
        return
    
    # Fetch additional PV information including EPICS fields
    pv_info = {
        "name": pv.pvname,
        "value": pv.value,
        "count": pv.count,
        "type": pv.type,
        "host": pv.host,
        "access": pv.access,
        "status": pv.status,
        "upper_alarm_limit": pv.upper_alarm_limit,
        "upper_ctrl_limit": pv.upper_ctrl_limit,
        "upper_disp_limit": pv.upper_disp_limit,
        "upper_warning_limit": pv.upper_warning_limit,
        "lower_alarm_limit": pv.lower_alarm_limit,
        "lower_ctrl_limit": pv.lower_ctrl_limit,
        "lower_disp_limit": pv.lower_disp_limit,
        "lower_warning_limit": pv.lower_warning_limit,
        "info": pv.info,
        "severity": pv.severity,
        "timestamp": pv.timestamp,
        "units": pv.units,
        "precision": pv.precision,
        "enum_strs": pv.enum_strs
    }

    # Fetch EPICS fields and handle empty values
    # pv_fields = {}
    # for field in epics_fields:
    #     try:
    #         value = pv.get(field)
    #         pv_fields[field] = value
    #     except Exception as e:
    #         print(f"Failed to fetch field \"{field}\" for PV {pv_name}: {e}")

    # Define the required properties
    
    # properties = ["value", "count", "type", "host", "access", "status", "severity", "timestamp", "units", "precision", "enum_strs"]

    # Create properties if they do not exist
    for key, value in pv_info.items():
         create_property_if_not_exists(cf_service_url, key, username, password)

    for tag in tags:
         create_tag_if_not_exists(cf_service_url, tag, username, password)

    # Define the channel data with required properties and EPICS fields
    channel_data = {
        "name": pv_name,
        "owner": username,  # Assuming the username is the owner
        "properties":[],
        "tags": [{"name": tag} for tag in tags]
    }

    # Add EPICS fields to properties
    for field, value in pv_info.items():
        vstr=str(value) if len(str(value)) else "NONE"
        channel_data["properties"].append({"name": field, "value": vstr})

    # Send a POST request to the Channel Finder Service to create the channel entry
    response = requests.post(
        f"{cf_service_url}/resources/channels",
        json=[channel_data],  # The API expects a list of channels
        auth=(username, password)
    )

    if response.status_code == 200:
        print(f"Successfully created channel entry for PV: {pv_name}")
    else:
        print(f"Failed to create channel entry for PV: {pv_name}. Status code: {response.status_code}, Response: {response.text}")

def process_directory(config_dir, cf_service_url, username, password):
    for root, dirs, files in os.walk(config_dir):
        for dir_name in dirs:
            pvlist_path = os.path.join(root, dir_name, 'pvlist.txt')
            if os.path.isfile(pvlist_path):
                with open(pvlist_path, 'r') as pv_file:
                    pv_names = [line.strip() for line in pv_file if line.strip()]
                    for pv_name in pv_names:
                        create_channel_entry(pv_name, [dir_name], cf_service_url, username, password)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Create Channel Finder entries for PVs listed in pvlist.txt files within a directory structure.')
    parser.add_argument('config_dir', type=str, help='The configuration directory containing subdirectories with pvlist.txt files.')
    parser.add_argument('cf_service_url', type=str, help='The Channel Finder service URL.')
    parser.add_argument('username', type=str, help='The username for the Channel Finder service.')
    parser.add_argument('password', type=str, help='The password for the Channel Finder service.')

    args = parser.parse_args()

    process_directory(args.config_dir, args.cf_service_url, args.username, args.password)
