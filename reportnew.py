import boto3
import requests
import os
import json
from datetime import datetime

# Define a list of regions
# , 'us-west-2', 'eu-west-1', 'ap-south-1'
regions = ['ap-southeast-1', 'us-west-2', 'eu-central-1']


def convert_datetime(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()  # Convert datetime to ISO 8601 string format
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


# Function to initialize the boto3 client
def get_inspector_client(region):
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')

    if aws_access_key_id and aws_secret_access_key:
        client = boto3.client(
            'inspector2',
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key
        )
    else:
        # If no environment variables, rely on the AWS CLI credentials configured via `aws configure`
        client = boto3.client(
            'inspector2',
            region_name=region
        )

    return client


# Function to get the latest successful scan in a region
def get_latest_successful_scan(region):
    client = get_inspector_client(region)

    # List scans in the given region
    response = client.list_cis_scans()

    pretty_json = json.dumps(response, indent=4, default=convert_datetime)
    print(pretty_json)

    # Check for successful scans
    successful_scans = []
    for scan in response.get('scans', []):
        if scan['status'] == 'COMPLETED':
            successful_scans.append(scan)

    # Sort by timestamp and return the latest scan ARN
    if successful_scans:
        latest_scan = max(successful_scans, key=lambda x: x['scanDate'])
        return latest_scan['scanArn']
    return None


# Function to download the scan report
def download_scan_report(region, scan_arn):
    client = get_inspector_client(region)
    # Request the report URL
    response = client.get_cis_scan_report(
        reportFormat='CSV',
        scanArn=scan_arn,
        targetAccounts=['468896299932']
    )

    pretty_json = json.dumps(response, indent=4, default=convert_datetime)
    print(f"url {pretty_json}")
    # Download the report
    if 'url' in response:
        r = response['url']
        res = requests.get(r)
        if res.status_code == 200:
            content = res.content
            with open(f'report_{region}.csv', 'wb') as file:
                file.write(content)
            print(f"Report downloaded for {region}")
        else:
            print(f"Failed to download report for {region}")
    else:
        print(f"No URL found for scan ARN {scan_arn} in {region}")


# Main logic to loop through regions and process each region
def process_scans():
    for region in regions:
        print(f"Processing region: {region}")
        scan_arn = get_latest_successful_scan(region)
        if scan_arn:
            print(f"Latest successful scan ARN found in {region}: {scan_arn}")
            download_scan_report(region, scan_arn)
        else:
            print(f"No successful scans found in {region}")


# Run the process
process_scans()
