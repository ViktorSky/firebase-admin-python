# Copyright 2020 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Firebase auth providers management sub module."""

from collections.abc import Callable
from typing import Any, Dict, Generic, List, Optional, cast
from typing_extensions import Self, TypeVar
from urllib import parse

import requests

from firebase_admin import _auth_utils
from firebase_admin import _http_client
from firebase_admin import _typing
from firebase_admin import _user_mgt

__all__ = (
    'MAX_LIST_CONFIGS_RESULTS',
    'ListProviderConfigsPage',
    'OIDCProviderConfig',
    'ProviderConfig',
    'ProviderConfigClient',
    'SAMLProviderConfig',
)

_ProviderConfigT = TypeVar('_ProviderConfigT', bound='ProviderConfig', default='ProviderConfig')


MAX_LIST_CONFIGS_RESULTS = 100


class ProviderConfig:
    """Parent type for all authentication provider config types."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    @property
    def provider_id(self) -> str:
        name = self._data['name']
        return name.split('/')[-1]

    @property
    def display_name(self) -> Optional[str]:
        return self._data.get('displayName')

    @property
    def enabled(self) -> bool:
        return self._data.get('enabled', False)


class OIDCProviderConfig(ProviderConfig):
    """Represents the OIDC auth provider configuration.

    See https://openid.net/specs/openid-connect-core-1_0-final.html.
    """

    @property
    def issuer(self):
        return self._data['issuer']

    @property
    def client_id(self):
        return self._data['clientId']

    @property
    def client_secret(self):
        return self._data.get('clientSecret')

    @property
    def id_token_response_type(self):
        return self._data.get('responseType', {}).get('idToken', False)

    @property
    def code_response_type(self):
        return self._data.get('responseType', {}).get('code', False)


class SAMLProviderConfig(ProviderConfig):
    """Represents he SAML auth provider configuration.

    See http://docs.oasis-open.org/security/saml/Post2.0/sstc-saml-tech-overview-2.0.html.
    """

    @property
    def idp_entity_id(self):
        return self._data['idpConfig']['idpEntityId']

    @property
    def sso_url(self):
        return self._data['idpConfig']['ssoUrl']

    @property
    def x509_certificates(self):
        certs = self._data['idpConfig']['idpCertificates']
        return [c['x509Certificate'] for c in certs]

    @property
    def callback_url(self):
        return self._data['spConfig']['callbackUri']

    @property
    def rp_entity_id(self):
        return self._data['spConfig']['spEntityId']


class ListProviderConfigsPage(Generic[_ProviderConfigT]):
    """Represents a page of ProviderConfig instances retrieved from a Firebase project.

    Provides methods for traversing the provider configs included in this page, as well as
    retrieving subsequent pages. The iterator returned by ``iterate_all()`` can be used to iterate
    through all provider configs in the Firebase project starting from this page.
    """

    def __init__(
        self,
        download: 'Callable[[Optional[str], int], Dict[str, _typing.Json]]',
        page_token: Optional[str],
        max_results: int,
    ) -> None:
        self._download = download
        self._max_results = max_results
        self._current = download(page_token, max_results)

    @property
    def provider_configs(self) -> List[_ProviderConfigT]:
        """A list of ``ProviderConfig`` instances available in this page."""
        raise NotImplementedError

    @property
    def next_page_token(self) -> str:
        """Page token string for the next page (empty string indicates no more pages)."""
        return cast(str, self._current.get('nextPageToken', ''))

    @property
    def has_next_page(self) -> bool:
        """A boolean indicating whether more pages are available."""
        return bool(self.next_page_token)

    def get_next_page(self) -> Optional[Self]:
        """Retrieves the next page of provider configs, if available.

        Returns:
            ListProviderConfigsPage: Next page of provider configs, or None if this is the last
            page.
        """
        if self.has_next_page:
            return self.__class__(self._download, self.next_page_token, self._max_results)
        return None

    def iterate_all(self) -> '_ProviderConfigIterator[_ProviderConfigT]':
        """Retrieves an iterator for provider configs.

        Returned iterator will iterate through all the provider configs in the Firebase project
        starting from this page. The iterator will never buffer more than one page of configs
        in memory at a time.

        Returns:
            iterator: An iterator of ProviderConfig instances.
        """
        return _ProviderConfigIterator(self)


class _ListOIDCProviderConfigsPage(ListProviderConfigsPage[OIDCProviderConfig]):
    @property
    def provider_configs(self) -> List[OIDCProviderConfig]:
        return [
            OIDCProviderConfig(data)
            for data in cast(
                List[Dict[str, Any]],
                self._current.get('oauthIdpConfigs', []),
            )
        ]


class _ListSAMLProviderConfigsPage(ListProviderConfigsPage[SAMLProviderConfig]):
    @property
    def provider_configs(self) -> List[SAMLProviderConfig]:
        return [
            SAMLProviderConfig(data)
            for data in cast(
                List[Dict[str, Any]],
                self._current.get('inboundSamlConfigs', []),
            )
        ]


class _ProviderConfigIterator(_auth_utils.PageIterator[ListProviderConfigsPage[_ProviderConfigT]]):
    @property
    def items(self) -> List[_ProviderConfigT]:
        return self._current_page.provider_configs if self._current_page else []


class ProviderConfigClient:
    """Client for managing Auth provider configurations."""

    PROVIDER_CONFIG_URL = 'https://identitytoolkit.googleapis.com/v2'

    def __init__(
        self,
        http_client: _http_client.HttpClient[Dict[str, _typing.Json]],
        project_id: str,
        tenant_id: Optional[str] = None,
        url_override: Optional[str] = None,
    ) -> None:
        self.http_client = http_client
        url_prefix = url_override or self.PROVIDER_CONFIG_URL
        self.base_url = '{0}/projects/{1}'.format(url_prefix, project_id)
        if tenant_id:
            self.base_url += '/tenants/{0}'.format(tenant_id)

    def get_oidc_provider_config(self, provider_id: str) -> OIDCProviderConfig:
        _validate_oidc_provider_id(provider_id)
        body = self._make_request('get', '/oauthIdpConfigs/{0}'.format(provider_id))
        return OIDCProviderConfig(body)

    def create_oidc_provider_config(
        self,
        provider_id: str,
        client_id: str,
        issuer: str,
        display_name: Optional[str] = None,
        enabled: Optional[bool] = None,
        client_secret: Optional[str] = None,
        id_token_response_type: Optional[bool] = None,
        code_response_type: Optional[bool] = None,
    ):
        """Creates a new OIDC provider config from the given parameters."""
        _validate_oidc_provider_id(provider_id)
        req: Dict[str, _typing.Json] = {
            'clientId': _validate_non_empty_string(client_id, 'client_id'),
            'issuer': _validate_url(issuer, 'issuer'),
        }
        if display_name is not None:
            req['displayName'] = _auth_utils.validate_string(display_name, 'display_name')
        if enabled is not None:
            req['enabled'] = _auth_utils.validate_boolean(enabled, 'enabled')

        response_type: Dict[str, _typing.Json] = {}
        if id_token_response_type is False and code_response_type is False:
            raise ValueError('At least one response type must be returned.')
        if id_token_response_type is not None:
            response_type['idToken'] = _auth_utils.validate_boolean(
                id_token_response_type, 'id_token_response_type')
        if code_response_type is not None:
            response_type['code'] = _auth_utils.validate_boolean(
                code_response_type, 'code_response_type')
            if code_response_type:
                req['clientSecret'] = _validate_non_empty_string(client_secret, 'client_secret')
        if response_type:
            req['responseType'] = response_type

        params = 'oauthIdpConfigId={0}'.format(provider_id)
        body = self._make_request('post', '/oauthIdpConfigs', json=req, params=params)
        return OIDCProviderConfig(body)

    def update_oidc_provider_config(
        self,
        provider_id: str,
        client_id: Optional[str] = None,
        issuer: Optional[str] = None,
        display_name: Optional[str] = None,
        enabled: Optional[bool] = None,
        client_secret: Optional[str] = None,
        id_token_response_type: Optional[bool] = None,
        code_response_type: Optional[bool] = None,
    ) -> OIDCProviderConfig:
        """Updates an existing OIDC provider config with the given parameters."""
        _validate_oidc_provider_id(provider_id)
        req: Dict[str, _typing.Json] = {}
        if display_name is not None:
            if display_name == _user_mgt.DELETE_ATTRIBUTE:
                req['displayName'] = None
            else:
                req['displayName'] = _auth_utils.validate_string(display_name, 'display_name')
        if enabled is not None:
            req['enabled'] = _auth_utils.validate_boolean(enabled, 'enabled')
        if client_id:
            req['clientId'] = _validate_non_empty_string(client_id, 'client_id')
        if issuer:
            req['issuer'] = _validate_url(issuer, 'issuer')

        response_type = {}
        if id_token_response_type is False and code_response_type is False:
            raise ValueError('At least one response type must be returned.')
        if id_token_response_type is not None:
            response_type['idToken'] = _auth_utils.validate_boolean(
                id_token_response_type, 'id_token_response_type')
        if code_response_type is not None:
            response_type['code'] = _auth_utils.validate_boolean(
                code_response_type, 'code_response_type')
            if code_response_type:
                req['clientSecret'] = _validate_non_empty_string(client_secret, 'client_secret')
        if response_type:
            req['responseType'] = response_type

        if not req:
            raise ValueError('At least one parameter must be specified for update.')

        update_mask = _auth_utils.build_update_mask(req)
        params = 'updateMask={0}'.format(','.join(update_mask))
        url = '/oauthIdpConfigs/{0}'.format(provider_id)
        body = self._make_request('patch', url, json=req, params=params)
        return OIDCProviderConfig(body)

    def delete_oidc_provider_config(self, provider_id: str) -> None:
        _validate_oidc_provider_id(provider_id)
        self._make_request('delete', '/oauthIdpConfigs/{0}'.format(provider_id))

    def list_oidc_provider_configs(
        self,
        page_token: Optional[str] = None,
        max_results: int = MAX_LIST_CONFIGS_RESULTS,
    ) -> _ListOIDCProviderConfigsPage:
        return _ListOIDCProviderConfigsPage(
            self._fetch_oidc_provider_configs, page_token, max_results)

    def _fetch_oidc_provider_configs(
        self,
        page_token: Optional[str] = None,
        max_results: int = MAX_LIST_CONFIGS_RESULTS,
    ) -> Dict[str, _typing.Json]:
        return self._fetch_provider_configs('/oauthIdpConfigs', page_token, max_results)

    def get_saml_provider_config(self, provider_id: str) -> SAMLProviderConfig:
        _validate_saml_provider_id(provider_id)
        body = self._make_request('get', '/inboundSamlConfigs/{0}'.format(provider_id))
        return SAMLProviderConfig(body)

    def create_saml_provider_config(
        self,
        provider_id: str,
        idp_entity_id: str,
        sso_url: str,
        x509_certificates: List[str],
        rp_entity_id: str,
        callback_url: str,
        display_name: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> SAMLProviderConfig:
        """Creates a new SAML provider config from the given parameters."""
        _validate_saml_provider_id(provider_id)
        req: Dict[str, Any] = {
            'idpConfig': {
                'idpEntityId': _validate_non_empty_string(idp_entity_id, 'idp_entity_id'),
                'ssoUrl': _validate_url(sso_url, 'sso_url'),
                'idpCertificates': _validate_x509_certificates(x509_certificates),
            },
            'spConfig': {
                'spEntityId': _validate_non_empty_string(rp_entity_id, 'rp_entity_id'),
                'callbackUri': _validate_url(callback_url, 'callback_url'),
            },
        }
        if display_name is not None:
            req['displayName'] = _auth_utils.validate_string(display_name, 'display_name')
        if enabled is not None:
            req['enabled'] = _auth_utils.validate_boolean(enabled, 'enabled')

        params = 'inboundSamlConfigId={0}'.format(provider_id)
        body = self._make_request('post', '/inboundSamlConfigs', json=req, params=params)
        return SAMLProviderConfig(body)

    def update_saml_provider_config(
        self,
        provider_id: str,
        idp_entity_id: Optional[str] = None,
        sso_url: Optional[str] = None,
        x509_certificates: Optional[List[str]]=None,
        rp_entity_id: Optional[str] = None,
        callback_url: Optional[str] = None,
        display_name: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> SAMLProviderConfig:
        """Updates an existing SAML provider config with the given parameters."""
        _validate_saml_provider_id(provider_id)
        idp_config: Dict[str, Any] = {}
        if idp_entity_id is not None:
            idp_config['idpEntityId'] = _validate_non_empty_string(idp_entity_id, 'idp_entity_id')
        if sso_url is not None:
            idp_config['ssoUrl'] = _validate_url(sso_url, 'sso_url')
        if x509_certificates is not None:
            idp_config['idpCertificates'] = _validate_x509_certificates(x509_certificates)

        sp_config: Dict[str, _typing.Json] = {}
        if rp_entity_id is not None:
            sp_config['spEntityId'] = _validate_non_empty_string(rp_entity_id, 'rp_entity_id')
        if callback_url is not None:
            sp_config['callbackUri'] = _validate_url(callback_url, 'callback_url')

        req: Dict[str, _typing.Json] = {}
        if display_name is not None:
            if display_name == _user_mgt.DELETE_ATTRIBUTE:
                req['displayName'] = None
            else:
                req['displayName'] = _auth_utils.validate_string(display_name, 'display_name')
        if enabled is not None:
            req['enabled'] = _auth_utils.validate_boolean(enabled, 'enabled')
        if idp_config:
            req['idpConfig'] = idp_config
        if sp_config:
            req['spConfig'] = sp_config

        if not req:
            raise ValueError('At least one parameter must be specified for update.')

        update_mask = _auth_utils.build_update_mask(req)
        params = 'updateMask={0}'.format(','.join(update_mask))
        url = '/inboundSamlConfigs/{0}'.format(provider_id)
        body = self._make_request('patch', url, json=req, params=params)
        return SAMLProviderConfig(body)

    def delete_saml_provider_config(self, provider_id: str) -> None:
        _validate_saml_provider_id(provider_id)
        self._make_request('delete', '/inboundSamlConfigs/{0}'.format(provider_id))

    def list_saml_provider_configs(
        self,
        page_token: Optional[str] = None,
        max_results: int = MAX_LIST_CONFIGS_RESULTS,
    ) -> _ListSAMLProviderConfigsPage:
        return _ListSAMLProviderConfigsPage(
            self._fetch_saml_provider_configs, page_token, max_results)

    def _fetch_saml_provider_configs(
        self,
        page_token: Optional[str] = None,
        max_results: int = MAX_LIST_CONFIGS_RESULTS,
    ) -> Dict[str, _typing.Json]:
        return self._fetch_provider_configs('/inboundSamlConfigs', page_token, max_results)

    def _fetch_provider_configs(
        self,
        path: str,
        page_token: Optional[str] = None,
        max_results: int = MAX_LIST_CONFIGS_RESULTS,
    ) -> Dict[str, _typing.Json]:
        """Fetches a page of auth provider configs"""
        if page_token is not None:
            if not isinstance(page_token, str) or not page_token:
                raise ValueError('Page token must be a non-empty string.')
        if not isinstance(max_results, int):
            raise ValueError('Max results must be an integer.')
        if max_results < 1 or max_results > MAX_LIST_CONFIGS_RESULTS:
            raise ValueError(
                'Max results must be a positive integer less than or equal to '
                '{0}.'.format(MAX_LIST_CONFIGS_RESULTS))

        params = 'pageSize={0}'.format(max_results)
        if page_token:
            params += '&pageToken={0}'.format(page_token)
        return self._make_request('get', path, params=params)

    def _make_request(self, method: str, path: str, **kwargs: Any) -> Dict[str, _typing.Json]:
        url = '{0}{1}'.format(self.base_url, path)
        try:
            return self.http_client.body(method, url, **kwargs)
        except requests.exceptions.RequestException as error:
            raise _auth_utils.handle_auth_backend_error(error)


def _validate_oidc_provider_id(provider_id: Any) -> str:
    if not isinstance(provider_id, str):
        raise ValueError(
            'Invalid OIDC provider ID: {0}. Provider ID must be a non-empty string.'.format(
                provider_id))
    if not provider_id.startswith('oidc.'):
        raise ValueError('Invalid OIDC provider ID: {0}.'.format(provider_id))
    return provider_id


def _validate_saml_provider_id(provider_id: Any) -> str:
    if not isinstance(provider_id, str):
        raise ValueError(
            'Invalid SAML provider ID: {0}. Provider ID must be a non-empty string.'.format(
                provider_id))
    if not provider_id.startswith('saml.'):
        raise ValueError('Invalid SAML provider ID: {0}.'.format(provider_id))
    return provider_id


def _validate_non_empty_string(value: Any, label: str) -> str:
    """Validates that the given value is a non-empty string."""
    if not isinstance(value, str):
        raise ValueError('Invalid type for {0}: {1}.'.format(label, value))
    if not value:
        raise ValueError('{0} must not be empty.'.format(label))
    return value


def _validate_url(url: Any, label: str) -> str:
    """Validates that the given value is a well-formed URL string."""
    if not isinstance(url, str) or not url:
        raise ValueError(
            'Invalid photo URL: "{0}". {1} must be a non-empty '
            'string.'.format(url, label))
    try:
        parsed = parse.urlparse(url)
        if not parsed.netloc:
            raise ValueError('Malformed {0}: "{1}".'.format(label, url))
        return url
    except Exception:
        raise ValueError('Malformed {0}: "{1}".'.format(label, url))


def _validate_x509_certificates(x509_certificates: Any) -> List[Dict[str, str]]:
    if not isinstance(x509_certificates, list) or not x509_certificates:
        raise ValueError('x509_certificates must be a non-empty list.')
    x509_certificates = cast(List[Any], x509_certificates)
    if not all([isinstance(cert, str) and cert for cert in x509_certificates]):
        raise ValueError('x509_certificates must only contain non-empty strings.')
    return [{'x509Certificate': cert} for cert in x509_certificates]
