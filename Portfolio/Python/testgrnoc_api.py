"""
Minimal test client for a GlobalNOC CDS node query over ECP/SAML.

All connection settings come from the environment. Nothing is hardcoded.

    export GN_HOST="https://cds.example.com/cds2/"
    export GN_USER="reporting-service@EXAMPLE.COM"
    export GN_PW="..."
    export GN_REALM="https://idp.example.com/idp/profile/SAML2/SOAP/ECP"
    export NODE_TYPES="1,2,3"

    python testgrnoc_api.py
"""

import os
import sys

import requests
import globalnoc.wsc

GN_HOST = os.getenv('GN_HOST', 'https://cds.example.com/cds2/')
GN_USER = os.getenv('GN_USER', 'reporting-service@EXAMPLE.COM')
GN_PW = os.getenv('GN_PW', '')
GN_REALM = os.getenv('GN_REALM', 'https://idp.example.com/idp/profile/SAML2/SOAP/ECP')
NODE_TYPES = [t for t in os.getenv('NODE_TYPES', '').split(',') if t]
TIMEOUT = int(os.getenv('GN_TIMEOUT', '15'))


def get_srv_hosts():
    session = requests.Session()

    params = [('method', 'get_nodes')]
    for node_role_id in NODE_TYPES:
        params.append(('node_role_id', node_role_id))

    response = session.get(
        GN_HOST + 'node.cgi',
        params=params,
        auth=globalnoc.wsc.ECP(GN_USER, GN_PW, GN_REALM),
        timeout=TIMEOUT,
    )

    print("Status:", response.status_code)
    print("Body:", response.text)


if __name__ == '__main__':
    if not GN_PW:
        sys.exit("GN_PW is not set. Export your credentials before running.")
    get_srv_hosts()
