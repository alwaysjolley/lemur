"""
.. module: lemur.plugins.lemur_fastly.plugin
    :platform: Unix
    :copyright: (c) 2020
    :license: Apache, see LICENCE for more details.

    Plugin for uploading certificates and private keys to fastly
    to use custom certificates.
    Faslty does not support pulling certificates out of the API,
    no source plugin could be built.

.. moduleauthor:: Christopher Jolley <chris@alwaysjolley.com>
"""
import json
import inspect
import hashlib
import requests
import time
from flask import current_app

from lemur.common.defaults import common_name
from lemur.common.utils import parse_certificate
from lemur.plugins.bases import DestinationPlugin

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization


class FastlyDestinationPlugin(DestinationPlugin):
    """Fastly Destination plugin for Lemur"""

    title = "Fastly"
    slug = "fastly-destination"
    description = "Allow for uploading of certificates to Fastly CDN service"

    author = "Christopher Jolley"
    author_url = "https://github.com/Netflix/lemur"

    options = []

    def __init__(self, *args, **kwargs):
        super(FastlyDestinationPlugin, self).__init__(*args, **kwargs)

    def upload(self, name, body, private_key, cert_chain, options, **kwargs):
        """
        Upload certificate and private key

        :param private_key:
        :param cert_chain:
        :return:
        """
        priv_id = None
        cert_id = None
        name_found = False
        cname = common_name(parse_certificate(body))
        priv_keys = get_all_private_keys()
        cert_keys = get_all_certificates()
        log_data = {
            "function": inspect.currentframe().f_code.co_name
        }
        for each in priv_keys:
            if each['name'] == cname:
                name_found = True
                if each['sha1'] == get_public_key_sha1(private_key):
                    priv_id = None
                    break
                else:
                    priv_id = each['id']
                    for cert in cert_keys:
                        if cert['name'] == cname:
                            cert_id = cert['id']
        if priv_id or not name_found:
            post_private_key(private_key, name=cname)
            time.sleep(2)
            new_cert_id = post_certificate(body, cert_chain, name=cname)
            log_data["message"] = f"Certificate updated: {cname}"
            act_id = get_activation(cert_id)
            if len(act_id) > 0 and new_cert_id:
                for each in act_id:
                    patch_activation(each, new_cert_id)
                    log_data["message"] = f"Certificate updated: {new_cert_id} activated: {act_id}"
            if cert_id:
                delete_certificate(cert_id)
            if priv_id:
                delete_private_key(priv_id)
        else:
            log_data["message"] = f"Certificate up to data, no changes made"
        current_app.logger.debug(log_data)


def get_public_key_sha1(private_key):
    """
    Resolves the public key sha1 hash

    :param private_key:
    :return hash:
    """
    private_obj = serialization.load_pem_private_key(
        private_key.encode("utf-8"),
        None,
        default_backend()
    )
    pub_key = private_obj.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha1(pub_key).hexdigest()  # nosec


def get_all_private_keys():
    """
    Return all private keys

    [
        {
            "id": "<HASH>",
            "name": "<CN>",
            "sha1": "<KEY_DIGEST>"
        }
    ]
    """
    p_keys = []
    path = '/tls/private_keys'
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        jdata = _get(path)
        for each in jdata['data']:
            p_keys.append(
                {
                    "id": each['id'],
                    "name": each['attributes']['name'],
                    "sha1": each['attributes']['public_key_sha1'],
                }
            )
    except Exception as err:
        log_data['message'] = 'Failure to get all private keys'
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return p_keys


def get_private_key(key_id):
    """
    Get and return a single private key

    {
      "data": {
        "id": "PRIVATE_KEY_ID",
        "type": "tls_private_key",
        "attributes": {
          "key_length": 2048,
          "key_type": "RSA",
          "name": "My private key",
          "created_at": "2019-02-01T12:12:12.000Z",
          "replace": false,
          "public_key_sha1": "KEY_DIGEST"
        }
      }
    }
    """
    jdata = {}
    path = f"/tls/private_keys/{key_id}"
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        jdata = _get(path)
    except Exception as err:
        log_data['message'] = f"Failure to get private key: {key_id}"
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return jdata


def post_private_key(private_key, name=None):
    """
    Upload private key

    {
      "data": {
        "type": "tls_private_key",
        "attributes": {
          "key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
          "name": "My private key"
        }
      }
    }
    """
    path = '/tls/private_keys'
    data = {
        "data": {
            "type": "tls_private_key",
            "attributes": {
                "key": private_key
            }
        }
    }
    jdata = {}
    priv_id = None
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    if name is not None:
        data['data']['attributes']['name'] = name
    try:
        jdata = _post(path, data)
        priv_id = jdata['data']['id']
        log_data['message'] = "private key upload: success"
    except Exception as err:
        log_data['message'] = "private key upload: failure"
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return priv_id


def delete_private_key(key_id):
    """
    Delete private key

    """
    path = f"/tls/private_keys/{key_id}"
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        _delete(path)
        log_data['message'] = 'delete: private key success'
    except Exception as err:
        log_data['message'] = 'delete: private key failure'
        log_data['error'] = err
    current_app.logger.debug(log_data)


def get_all_certificates():
    """
    Return all public certs

    returns list of dict
    - id: key_id for looking up certificates
    - name: Common name of the certificate
    """
    certs = []
    path = '/tls/certificates'
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        jdata = _get(path)
        for each in jdata['data']:
            certs.append({"id": each['id'], "name": each['attributes']['name']})
    except Exception as err:
        log_data['message'] = "Failure to get all public keys"
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return certs


def get_certificate(key_id):
    """
    Return single public certs

    example:
    {
      "data": {
        "id": "TLS_CERTIFICATE_ID",
        "type": "tls_certificate",
        "attributes": {
          "created_at": "2019-02-01T12:12:12.000Z",
          "issued_to": "...",
          "issuer": "Let's Encrypt Authority X3",
          "name": "My certificate",
          "not_after": "2020-02-01T12:12:12.000Z",
          "not_before": "2019-02-01T12:12:12.000Z",
          "replace": false,
          "serial_number": "1234567890",
          "signature_algorithm": "SHA256",
          "updated_at": "2019-02-01T12:12:12.000Z"
        },
        "relationships": {
          "tls_domains": {
            "data": [
              { "id": "DOMAIN_NAME", "type": "tls_domain" }
            ]
          }
        }
      }
    }
    """
    jdata = {}
    path = f"/tls/certificates/{key_id}"
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        jdata = _get(path)
        log_data['message'] = f"get certificate {key_id} success"
    except Exception as err:
        log_data['message'] = f"get certificate {key_id} failure"
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return jdata


def post_certificate(cert, chain, name=None):
    """upload public certificate"""
    path = '/tls/certificates'
    full_cert = f"{cert}\n{chain}"
    cert_id = None
    data = {
        "data": {
            "type": "tls_certificate",
            "attributes": {
                "cert_blob": full_cert
            }
        }
    }
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    if name:
        data['data']['attributes']['name'] = name
    try:
        jdata = _post(path, data)
        cert_id = jdata['data']['id']
        log_data['message'] = f"certificate upload {name} success"
    except Exception as err:
        log_data['message'] = f"certificate upload {name} failure"
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return cert_id


def patch_certificate(cert, chain, key_id):
    """replace existing public certificate"""
    path = f"/tls/certificates/{key_id}"
    full_cert = f"{cert}\n{chain}"
    data = {
        "data": {
            "type": "tls_certificate",
            "attributes": {
                "cert_blob": full_cert,
            }
        }
    }
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        _patch(path, data)
        log_data['message'] = f"certificate update {key_id} success"
    except Exception as err:
        log_data['message'] = f"certificate update {key_id} failure"
        log_data['error'] = err
    current_app.logger.debug(log_data)


def delete_certificate(key_id):
    """delete existing public certificate"""
    path = f"/tls/certificates/{key_id}"
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        _delete(path)
        log_data['message'] = f"certificate delete {key_id} success"
    except Exception as err:
        log_data['message'] = f"certificate delete {key_id} failure"
        log_data['error'] = err
    current_app.logger.debug(log_data)


def get_all_activations():
    """get all the activations"""
    path = '/tls/activations'
    jdata = {}
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        jdata = _get(path)
        log_data['message'] = 'activation get success'
    except Exception as err:
        log_data['message'] = 'activation get failure'
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return jdata


def get_activation(cert_id):
    """get activation by certificate id"""
    path = '/tls/activations'
    jdata = {}
    act_id = []
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    try:
        jdata = _get(path)
        for each in jdata['data']:
            if each['relationships']['tls_certificate']['data']['id'] == cert_id:
                act_id.append(each['id'])
        log_data['message'] = 'activation get success'
    except Exception as err:
        log_data['message'] = 'activation get failure'
        log_data['error'] = err
    current_app.logger.debug(log_data)
    return act_id


def patch_activation(act_id, cert_id):
    """update activation to use new certificate"""
    path = f"/tls/activations/{act_id}"
    log_data = {
        "function": inspect.currentframe().f_code.co_name
    }
    data = {
        "data": {
            "type": "tls_activation",
            "relationships": {
                "tls_certificate": {
                    "data": {"type": "tls_certificate", "id": cert_id}
                }
            }
        }
    }
    try:
        _patch(path, data)
        log_data['message'] = f"activation patch {act_id} success"
    except Exception as err:
        log_data['message'] = f"activation pathc {act_id} failure"
        log_data['error'] = err
    current_app.logger.debug(log_data)


def _generate_header():
    """ return preconfigured headers """
    headers = {
        'Fastly-Key': current_app.config.get("FASTLY_KEY"),
        'Content-Type': 'application/vnd.api+json',
        'Accept': 'application/vnd.api+json',
    }
    return headers


def _get(path, params=None):
    """
    Execute a GET request on the given URL (base_uri + path) and return response as JSON object

    :param path: Relative URL path
    :param params: additional parameters
    :return: json response
    """
    base_uri = 'https://api.fastly.com'
    resp = requests.get(
        f"{base_uri}{path}",
        headers=_generate_header(),
        params=params,
        verify=True
    )
    resp.raise_for_status()
    return resp.json()


def _post(path, payload):
    """
    Execute a put request on the given URL (base_uri + path) with given payload

    :param path:
    :param payload:
    :return:
    """
    base_uri = 'https://api.fastly.com'
    resp = requests.post(
        f"{base_uri}{path}",
        data=json.dumps(payload),
        headers=_generate_header(),
        verify=True
    )
    resp.raise_for_status()
    return resp.json()


def _patch(path, payload):
    """
    Execute a Patch request on the given URL (base_uri + path) with given payload

    :param path:
    :param payload:
    :return:
    """
    base_uri = 'https://api.fastly.com'
    resp = requests.patch(
        f"{base_uri}{path}",
        data=json.dumps(payload),
        headers=_generate_header(),
        verify=True
    )
    resp.raise_for_status()


def _delete(path):
    """
    Execute a Delete requests on the given URL (base_uri + path)

    """
    base_uri = 'https://api.fastly.com'
    resp = requests.delete(
        f"{base_uri}{path}",
        headers=_generate_header(),
        verify=True
    )
    resp.raise_for_status()
