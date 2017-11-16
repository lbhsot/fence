from __future__ import print_function

from datetime import datetime, timedelta
import uuid

from cdispyutils.log import get_logger
import flask
from flask import render_template, jsonify, request
from flask_oauthlib.provider import OAuth2Provider
from flask_sqlalchemy_session import current_session
import jwt
import oauthlib

from . import models
from .auth import get_current_user
from .jwt_validator import JWTValidator
from .utils import hash_secret


log = get_logger('fence')


oauth = OAuth2Provider()


@oauth.grantgetter
def load_grant(client_id, code):
    return (
        current_session
        .query(models.Grant)
        .filter_by(client_id=client_id, code=code)
        .first()
    )


@oauth.clientgetter
def load_client(client_id):
    return (
        current_session
        .query(models.Client)
        .filter_by(client_id=client_id)
        .first()
    )


@oauth.grantsetter
def save_grant(client_id, code, request, *args, **kwargs):
    # decide the expires time yourself
    expires = datetime.utcnow() + timedelta(seconds=100)
    grant = models.Grant(
        client_id=client_id,
        code=code['code'],
        redirect_uri=request.redirect_uri,
        _scopes=' '.join(request.scopes),
        user=get_current_user(),
        expires=expires
    )
    current_session.add(grant)
    current_session.commit()
    return grant


@oauth.tokensetter
def save_token(token, request, *args, **kwargs):
    toks = current_session.query(models.Token).filter_by(
        client_id=request.client.client_id,
        user_id=request.user.id)
    # make sure that every client has only one token connected to a user
    for t in toks:
        current_session.delete(t)

    expires_in = token.get('expires_in')
    expires = datetime.utcnow() + timedelta(seconds=expires_in)

    tok = models.Token(
        access_token=token['access_token'],
        refresh_token=token['refresh_token'],
        token_type=token['token_type'],
        _scopes=token['scope'],
        expires=expires,
        client_id=request.client.client_id,
        user_id=request.user.id,
    )
    current_session.add(tok)
    current_session.commit()
    return tok


@oauth.tokengetter
def load_token(access_token=None, refresh_token=None):
    if access_token:
        return (
            current_session
            .query(models.Token)
            .filter_by(access_token=access_token)
            .first()
        )
    elif refresh_token:
        return (
            current_session
            .query(models.Token)
            .filter_by(refresh_token=refresh_token)
            .first()
        )


# Redefine the request validator used by the OAuth provider, using the
# JWTValidator which redefines bearer and refresh token validation to use JWT.
oauth._validator = JWTValidator(
    clientgetter=oauth._clientgetter,
    tokengetter=oauth._tokengetter,
    grantgetter=oauth._grantgetter,
    usergetter=None,
    tokensetter=oauth._tokensetter,
    grantsetter=oauth._grantsetter,
)


def signed_access_token_generator(private_key, **kwargs):
    """
    Return a function which takes in an oauthlib request and generates a signed
    JWT access token. This function should be assigned as the access token
    generator for the flask app:

    .. code-block:: python

        app.config['OAUTH2_PROVIDER_TOKEN_GENERATOR'] = (
            signed_access_token_generator(private_key)
        )

    Return:
        Callable[[oauthlib.common.Request], str]
    """
    def signed_access_token_generator(request):
        """
        Args:
            request (oauthlib.common.Request)

        Return:
            str: encoded JWT signed with ``private_key``
        """
        return generate_signed_access_token(private_key, request)
    return signed_access_token_generator


def signed_refresh_token_generator(private_key, **kwargs):
    """
    Return a function which takes in an oauthlib request and generates a signed
    JWT refresh token. This function should be assigned as the refresh token
    generator for the flask app:

    .. code-block:: python

        app.config['OAUTH2_PROVIDER_REFRESH_TOKEN_GENERATOR'] = (
            signed_refresh_token_generator(private_key)
        )

    Return:
        Callable[[oauthlib.common.Request], str]
    """
    def signed_refresh_token_generator(request):
        """
        Args:
            request (oauthlib.common.Request)

        Return:
            str: encoded JWT signed with ``private_key``
        """
        return generate_signed_refresh_token(private_key, request)
    return signed_refresh_token_generator


def generate_signed_refresh_token(private_key, request):
    """
    Generate a JWT refresh token from the given request, and output a UTF-8
    string of the encoded JWT signed with the private key.

    Args:
        private_key (str): RSA private key to sign and encode the JWT with
        request (oauthlib.common.Request): token request to handle

    Return:
        str: encoded JWT signed with ``private_key``
    """
    now = datetime.utcnow()
    claims = {
        'sub': request.user.id,
        'iss': flask.current_app.config.get('HOST_NAME'),
        'iat': now,
        'exp': now + timedelta(seconds=request.expires_in),
        'jti': str(uuid.uuid4()),
        'context': {
            'user': {
                'name': request.user,
            }
        },
    }
    claims.update(request.claims)
    token = jwt.encode(claims, private_key, algorithm='RS256')
    token = oauthlib.common.to_unicode(token, 'UTF-8')
    return token


def generate_signed_access_token(private_key, request):
    """
    Generate a JWT refresh token from the given request, and output a UTF-8
    string of the encoded JWT signed with the private key.

    Args:
        private_key (str): RSA private key to sign and encode the JWT with
        request (oauthlib.common.Request): token request to handle
    """
    now = datetime.utcnow()
    raise RuntimeError(str(request))
    claims = {
        'sub': request.user.id,
        'iss': flask.current_app.config.get('HOST_NAME'),
        'iat': now,
        'exp': now + timedelta(seconds=request.expires_in),
        'jti': str(uuid.uuid4()),
        'context': {
            'user': {
                'name': request.user,
            }
        },
    }
    claims.update(request.claims)
    token = jwt.encode(claims, private_key, algorithm='RS256')
    token = oauthlib.common.to_unicode(token, 'UTF-8')
    return token


def init_oauth(app):
    private_key = app.keys.items()[0][1]
    app.config['OAUTH2_PROVIDER_REFRESH_TOKEN_GENERATOR'] = (
        signed_refresh_token_generator(private_key)
    )
    app.config['OAUTH2_PROVIDER_TOKEN_GENERATOR'] = (
        signed_access_token_generator(private_key)
    )
    app.config['OAUTH2_PROVIDER_TOKEN_EXPIRES_IN'] = 3600
    oauth.init_app(app)


blueprint = flask.Blueprint('oauth2', __name__)


@blueprint.route('/authorize', methods=['GET', 'POST'])
@oauth.authorize_handler
def authorize(*args, **kwargs):
    if request.method == 'GET':
        client_id = kwargs.get('client_id')
        client = (
            current_session
            .query(models.Client)
            .filter_by(client_id=client_id)
            .first()
        )
        if client.auto_approve:
            return True
        kwargs['client'] = client
        return render_template('oauthorize.html', **kwargs)

    confirm = request.form.get('confirm', 'no')
    return confirm == 'yes'


@blueprint.route('/token', methods=['POST'])
@hash_secret
@oauth.token_handler
def access_token(*args, **kwargs):
    """
    Handle exchanging and refreshing the access token.

    The operation here is handled entirely by the ``oauth.token_handler``
    decorator, so this function only needs to pass.
    """
    pass


@blueprint.route('/revoke', methods=['POST'])
@oauth.revoke_handler
def revoke_token():
    """
    Revoke the access given to an application.

    The operation is handled by the ``oauth.revoke_handler`` decorator, so this
    function just passes.
    """
    pass


@blueprint.route('/errors', methods=['GET'])
def display_error():
    return jsonify(request.args)
