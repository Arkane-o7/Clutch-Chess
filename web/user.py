import json
import random
import requests
import string
import traceback
import uuid

from flask import Blueprint, request, redirect, session, url_for
from flask_login import current_user, login_user, logout_user
from authlib.integrations.flask_client import OAuth
from sqlalchemy.exc import IntegrityError

import config
from db import db_service, s3


ANIMALS = ['Tiger', 'Leopard', 'Crane', 'Snake', 'Dragon']
CHESS_PIECES = ['Pawn', 'Knight', 'Bishop', 'Rook', 'Queen', 'King']

PROFILE_PIC_SIZE_LIMIT = 1024 * 64

oauth = OAuth()

user = Blueprint('user', __name__)


def init_oauth(app):
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile'
        }
    )


@user.route('/login', methods=['GET'])
def login():
    next_url = request.args.get('next') or url_for('index')
    print('login', request.args)

    if current_user.is_authenticated:
        return redirect(next_url)

    callback = url_for('user.authorized', _external=True)
    if 'fly.dev' in callback or 'kfchess.com' in callback:
        callback = callback.replace('http://', 'https://')

    session['oauth_next_url'] = next_url
    return oauth.google.authorize_redirect(callback)


@user.route('/api/user/oauth2callback', methods=['GET'])
def authorized():
    next_url = session.pop('oauth_next_url', url_for('index'))
    print('oauth authorized', request.args)

    if current_user.is_authenticated:
        return redirect(next_url)

    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if user_info:
            email = user_info.get('email')
            
            if email:
                print('user data', user_info)
                
                user = db_service.get_user_by_email(email)
                if user is None:
                    # create user with random username
                    username = random_username()
                    while db_service.get_user_by_username(username) is not None:
                        username = random_username()

                    user = db_service.create_user(email, username, None, {})

                login_user(user)
            else:
                print('error: no email in user info')
        else:
            print('error: no user info in token')
    except Exception as e:
        print('error getting google info', str(e))
        traceback.print_exc()

    return redirect(next_url)


@user.route('/logout', methods=['POST'])
def logout():
    print('logout', current_user)

    logout_user()

    csrf_token = generate_csrf_token()

    return json.dumps({
        'loggedIn': False,
        'csrfToken': csrf_token,
    })


@user.route('/api/user/info', methods=['GET'])
def info():
    user_ids = request.args.getlist('userId')
    print('user info', user_ids)

    if not user_ids:
        csrf_token = generate_csrf_token()

        # look up my info
        if not current_user.is_authenticated:
            return json.dumps({
                'loggedIn': False,
                'csrfToken': csrf_token,
            })

        return json.dumps({
            'loggedIn': True,
            'csrfToken': csrf_token,
            'user': current_user.to_json_obj(with_key=True),
        });

    # look up other user info
    users = db_service.get_users_by_id(user_ids)
    return json.dumps({
        'users': {
            user_id: user.to_json_obj()
            for user_id, user in users.items()
        },
    })


@user.route('/api/user/update', methods=['POST'])
def update():
    data = json.loads(request.data)
    print('user update', data)

    if not current_user.is_authenticated:
        return json.dumps({
            'success': False,
            'message': 'User is not logged in.',
        })

    user_id = current_user.user_id
    user = db_service.get_user_by_id(user_id)
    if user is None:
        return json.dumps({
            'success': False,
            'message': 'User does not exist.',
        })

    user.username = data.get('username', user.username)

    if len(user.username) < 3:
        return json.dumps({
            'success': False,
            'message': 'Username too short.',
        })
    elif len(user.username) > 24:
        return json.dumps({
            'success': False,
            'message': 'Username too long.',
        })

    try:
        db_service.update_user(user_id, user.username, user.picture_url)
        user = db_service.get_user_by_id(user_id)
        response = {
            'success': True,
            'user': user.to_json_obj(),
        }
    except IntegrityError:
        response = {
            'success': False,
            'message': 'Username already taken.',
        }

    return json.dumps(response)


@user.route('/api/user/uploadPic', methods=['POST'])
def upload_pic():
    file_bytes = request.data
    print('upload pic', len(file_bytes))

    if not current_user.is_authenticated:
        return json.dumps({
            'success': False,
            'message': 'User is not logged in.',
        })

    user_id = current_user.user_id
    user = db_service.get_user_by_id(user_id)
    if user is None:
        return json.dumps({
            'success': False,
            'message': 'User does not exist.',
        })

    if len(file_bytes) > PROFILE_PIC_SIZE_LIMIT:
        return json.dumps({
            'success': False,
            'message': 'File is too large (max size 64KB).',
        })

    try:
        key = 'profile-pics/' + str(uuid.uuid4())
        s3.upload_data('com-kfchess-public', key, file_bytes, ACL='public-read')
        url = s3.get_public_url('com-kfchess-public', key)
        print('s3 upload', key, url)

        db_service.update_user(user_id, user.username, url)
        user = db_service.get_user_by_id(user_id)
        response = {
            'success': True,
            'user': user.to_json_obj(),
        }
    except:
        traceback.print_exc()
        response = {
            'success': False,
            'message': 'Failed to upload profile picture.',
        }

    return json.dumps(response)


@user.route('/api/user/history', methods=['GET'])
def history():
    user_id = int(request.args['userId'])
    offset = int(request.args['offset'])
    count = int(request.args['count'])
    print('history', request.args)

    history = db_service.get_user_game_history(user_id, offset, count)

    # fetch user info for all opponents in history
    user_ids = set()
    for h in history:
        for value in h.game_info['opponents']:
            if value.startswith('u:'):
                user_ids.add(int(value[2:]))

    if user_ids:
        users = db_service.get_users_by_id(list(user_ids))
    else:
        users = {}

    return json.dumps({
        'history': [
            h.to_json_obj() for h in history
        ],
        'users': {
            user_id: user.to_json_obj()
            for user_id, user in users.items()
        },
    })


@user.route('/api/user/campaign', methods=['GET'])
def campaign():
    user_id = int(request.args['userId'])
    print('campaign')

    progress = db_service.get_campaign_progress(user_id)
    return json.dumps({
        'progress': progress.to_json_obj(),
    })


def random_username():
    return random.choice(ANIMALS) + ' ' + random.choice(CHESS_PIECES) + ' ' + str(random.randint(100, 999))


def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = ''.join(random.choice(string.ascii_uppercase + string.digits) for i in range(24))
    return session['_csrf_token']
