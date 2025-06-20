# Copyright 2022 Google Inc.
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

"""Firebase App Check module."""

from typing import Any, Dict, Optional, cast

import jwt

import firebase_admin
from firebase_admin import _utils

__all__ = ('verify_token',)


_APP_CHECK_ATTRIBUTE = '_app_check'


def _get_app_check_service(app: Optional[firebase_admin.App]) -> '_AppCheckService':
    return _utils.get_app_service(app, _APP_CHECK_ATTRIBUTE, _AppCheckService)


def verify_token(
    token: str,
    app: Optional[firebase_admin.App] = None,
) -> Dict[str, Any]:
    """Verifies a Firebase App Check token.

    Args:
        token: A token from App Check.
        app: An App instance (optional).

    Returns:
        Dict[str, Any]: The token's decoded claims.

    Raises:
        ValueError: If the app's ``project_id`` is invalid or unspecified,
        or if the token's headers or payload are invalid.
        PyJWKClientError: If PyJWKClient fails to fetch a valid signing key.
    """
    return _get_app_check_service(app).verify_token(token)


class _AppCheckService:
    """Service class that implements Firebase App Check functionality."""

    _APP_CHECK_ISSUER = 'https://firebaseappcheck.googleapis.com/'
    _JWKS_URL = 'https://firebaseappcheck.googleapis.com/v1/jwks'

    _APP_CHECK_HEADERS = {
        'x-goog-api-client': _utils.get_metrics_header(),
    }

    def __init__(self, app: firebase_admin.App) -> None:
        # Validate and store the project_id to validate the JWT claims
        if not app.project_id:
            raise ValueError(
                'A project ID must be specified to access the App Check '
                'service. Either set the projectId option, use service '
                'account credentials, or set the '
                'GOOGLE_CLOUD_PROJECT environment variable.')
        self._project_id = app.project_id
        self._scoped_project_id = 'projects/' + app.project_id
        # Default lifespan is 300 seconds (5 minutes) so we change it to 21600 seconds (6 hours).
        self._jwks_client = jwt.PyJWKClient(
            self._JWKS_URL, lifespan=21600, headers=self._APP_CHECK_HEADERS)

    def verify_token(self, token: str) -> Dict[str, Any]:
        """Verifies a Firebase App Check token."""
        _Validators.check_string("app check token", token)

        # Obtain the Firebase App Check Public Keys
        # Note: It is not recommended to hard code these keys as they rotate,
        # but you should cache them for up to 6 hours.
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            self._has_valid_token_headers(jwt.get_unverified_header(token))
            verified_claims = self._decode_and_verify(token, signing_key.key)
        except (jwt.InvalidTokenError, jwt.DecodeError) as exception:
            raise ValueError(
                f'Verifying App Check token failed. Error: {exception}'
                )

        verified_claims['app_id'] = verified_claims.get('sub')
        return verified_claims

    def _has_valid_token_headers(self, headers: Dict[str, Any]) -> None:
        """Checks whether the token has valid headers for App Check."""
        # Ensure the token's header has type JWT
        if headers.get('typ') != 'JWT':
            raise ValueError("The provided App Check token has an incorrect type header")
        # Ensure the token's header uses the algorithm RS256
        algorithm = headers.get('alg')
        if algorithm != 'RS256':
            raise ValueError(
                'The provided App Check token has an incorrect alg header. '
                f'Expected RS256 but got {algorithm}.'
                )

    def _decode_and_verify(self, token: str, signing_key: str) -> Dict[str, Any]:
        """Decodes and verifies the token from App Check."""
        payload: Dict[str, Any] = {}
        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._scoped_project_id
            )
        except jwt.InvalidSignatureError:
            raise ValueError(
                'The provided App Check token has an invalid signature.'
                )
        except jwt.InvalidAudienceError:
            raise ValueError(
                'The provided App Check token has an incorrect "aud" (audience) claim. '
                f'Expected payload to include {self._scoped_project_id}.'
                )
        except jwt.InvalidIssuerError:
            raise ValueError(
                'The provided App Check token has an incorrect "iss" (issuer) claim. '
                f'Expected claim to include {self._APP_CHECK_ISSUER}'
                )
        except jwt.ExpiredSignatureError:
            raise ValueError(
                'The provided App Check token has expired.'
                )
        except jwt.InvalidTokenError as exception:
            raise ValueError(
                f'Decoding App Check token failed. Error: {exception}'
                )

        audience = payload.get('aud')
        if not isinstance(audience, list) or self._scoped_project_id not in audience:
            raise ValueError('Firebase App Check token has incorrect "aud" (audience) claim.')
        if not cast(str, payload['iss']).startswith(self._APP_CHECK_ISSUER):
            raise ValueError('Token does not contain the correct "iss" (issuer).')
        _Validators.check_string(
            'The provided App Check token "sub" (subject) claim',
            payload.get('sub'))

        return payload


class _Validators:
    """A collection of data validation utilities.

    Methods provided in this class raise ``ValueErrors`` if any validations fail.
    """

    @classmethod
    def check_string(cls, label: str, value: Any) -> None:
        """Checks if the given value is a string."""
        if value is None:
            raise ValueError('{0} "{1}" must be a non-empty string.'.format(label, value))
        if not isinstance(value, str):
            raise ValueError('{0} "{1}" must be a string.'.format(label, value))
