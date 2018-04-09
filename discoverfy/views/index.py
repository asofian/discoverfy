"""
Discoverfy index (main) view.

URLs include:
/
"""
import json
import flask
from flask import Flask, request, redirect, render_template, url_for, session
import requests
import base64
import urllib
import discoverfy
import arrow
import sqlite3
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import apscheduler
import os

#  Client Keys
CLIENT_ID = '15b472bff56a463781420db9f55bcf7d'
CLIENT_SECRET = '100bf466b477416883f8f581f179cd43'

# Spotify URLS
SPOTIFY_AUTH_URL = 'https://accounts.spotify.com/authorize'
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API_BASE_URL = 'https://api.spotify.com'
API_VERSION = 'v1'
SPOTIFY_API_URL = '{}/{}'.format(SPOTIFY_API_BASE_URL, API_VERSION)

# Server-side Parameters
CLIENT_SIDE_URL = 'http://localhost'
PORT = 8000
REDIRECT_URI = '{}:{}/callback'.format(CLIENT_SIDE_URL, PORT)
SCOPE = 'playlist-modify-public playlist-modify-private playlist-read-private user-top-read'

auth_query_parameters = {
    'response_type': 'code',
    'redirect_uri': REDIRECT_URI,
    'scope': SCOPE,
    'client_id': CLIENT_ID
}

def weekly_task():
    with discoverfy.app.app_context():
        if not discoverfy.app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true": #for demo, to prevent scheduler from running twice every IntervalTrigger in debug mode
            database = discoverfy.model.get_db()
            cursor = database.cursor()
            cursor.execute('''
                       SELECT *
                       FROM users
                       ''')
            result = cursor.fetchall()

            # Weekly work for each user
            for i in result:
                print(i)

                # Get new access token using refresh token
                refresh_token = i['refresh_token']
                print("REFRESH TOKEN")
                print(refresh_token)
                code_payload = {
                    'grant_type': 'refresh_token',
                    'refresh_token': str(refresh_token),
                    'redirect_uri': REDIRECT_URI,
                    'client_id' : CLIENT_ID, # ALTERNATIVE METHOD
                    'client_secret' : CLIENT_SECRET # ALTERNATIVE METHOD
                }
                headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                post_request = requests.post(SPOTIFY_TOKEN_URL, data=code_payload, headers=headers)

                #TODO If request fails due to revoked access, remove user from database

                # Tokens are Returned to Application
                response_data = json.loads(post_request.text)
                print(response_data)
                access_token = response_data['access_token']
                # refresh_token = response_data['refresh_token']
                # no refresh token returned, however initial refresh token should be valid until access revoked (?)
                token_type = response_data['token_type']
                expires_in = response_data['expires_in']

                # Use the access token to access Spotify API
                authorization_header = {'Authorization':'Bearer {}'.format(access_token)}

                # Get profile data
                user_profile_api_endpoint = '{}/me/'.format(SPOTIFY_API_URL)
                profile_response = requests.get(user_profile_api_endpoint, headers=authorization_header)
                profile_data = json.loads(profile_response.text)

                # Get user playlist data
                playlist_api_endpoint = '{}/playlists/'.format(profile_data['href'])
                playlists_response = requests.get(playlist_api_endpoint, headers=authorization_header)
                playlist_data = json.loads(playlists_response.text)


                # Create new playlist for user (do_the_thing)

                do_the_thing(playlist_data, access_token, i)

                # Update database with new refresh token (API manual says new refresh token may be returned)

    print ("debug")

scheduler = BackgroundScheduler()
scheduler.start()
scheduler.add_job(
    func=weekly_task,
    trigger=IntervalTrigger(seconds=20),
    id='main_task',
    name='weekly_task',
    replace_existing=True)
# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())


@discoverfy.app.route('/', methods=['GET', 'POST'])
def show_index():
    """Display / route."""
    if 'username' in session:
        return redirect(url_for('show_user', user_id=session['username']))
    elif request.method == 'POST':
        url_args = ''
        for key, val in auth_query_parameters.items():
            url_args += '{}={}&'.format(key, val)
        auth_url = '{}?{}'.format(SPOTIFY_AUTH_URL, url_args)
        return redirect(auth_url)
    return render_template('index.html')


def add_user_to_db(user_id, refresh_token):
    database = discoverfy.model.get_db()
    cursor = database.cursor()
    """Add new user to database."""
    cursor.execute('''
                   INSERT INTO users(user_id, refresh_token)
                   VALUES("{}",
                          "{}")
                   '''.format(user_id, refresh_token))
    print("END OF add_user_to_db")


@discoverfy.app.route('/callback/')
def callback():
    """Get authentication tokens."""
    # Requests refresh and access tokens
    auth_token = request.args['code']
    code_payload = {
        'grant_type': 'authorization_code',
        'code': str(auth_token),
        'redirect_uri': REDIRECT_URI,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    post_request = requests.post(SPOTIFY_TOKEN_URL, data=code_payload, headers=headers)

    # Tokens are Returned to Application
    response_data = json.loads(post_request.text)
    access_token = response_data['access_token']
    refresh_token = response_data['refresh_token']
    token_type = response_data['token_type']
    expires_in = response_data['expires_in']

    # Use the access token to access Spotify API
    authorization_header = {'Authorization':'Bearer {}'.format(access_token)}

    # Get profile data
    user_profile_api_endpoint = '{}/me/'.format(SPOTIFY_API_URL)
    profile_response = requests.get(user_profile_api_endpoint, headers=authorization_header)
    profile_data = json.loads(profile_response.text)

    # Get user playlist data
    playlist_api_endpoint = '{}/playlists/'.format(profile_data['href'])
    playlists_response = requests.get(playlist_api_endpoint, headers=authorization_header)
    playlist_data = json.loads(playlists_response.text)

    # Add user to database
    try:
        add_user_to_db(profile_data['id'], refresh_token)

        database = discoverfy.model.get_db()
        cursor = database.cursor()

        cursor.execute('''
                       SELECT *
                       FROM users
                       WHERE user_id="{}"
                       '''.format(profile_data['id']))
        user = cursor.fetchone()

        # Save Discover Weekly playlist
        do_the_thing(playlist_data, access_token, user)
    except(sqlite3.IntegrityError) as e:
        print(e)

    session['username'] = profile_data['id']

    return redirect(url_for('show_user', user_id=profile_data['id']))


@discoverfy.app.route('/u/<user_id>/', methods=['GET', 'POST'])
def show_user(user_id):
    """Display /u/<user_id> route."""
    return render_template('user.html', user=user_id)

def do_the_thing(playlist_data, access_token, user):
    user_id = user['user_id']

    headers = {
        'Content-Type': 'application/json',
        'Authorization':'Bearer {}'.format(access_token)
    }

    discover_weekly_tracks_link = ''
    discover_weekly_found = False
    while not discover_weekly_found:
        for playlist in playlist_data['items']:
            if playlist['name'] == 'Discover Weekly' and playlist['owner']['id'] == 'spotify':
                discover_weekly_tracks_link = playlist['tracks']['href']
                discover_weekly_found = True
                break

        if playlist_data['next']:
            next_playlist_link = playlist_data['next']
            playlists_response = requests.get(next_playlist_link, headers=headers)
            playlist_data = json.loads(playlists_response.text)


    tracks_response = requests.get(discover_weekly_tracks_link, headers=headers)
    tracks_data = json.loads(tracks_response.text)

    track_uris = []
    for track in tracks_data['items']:
        track_uris.append(track['track']['uri'])

    # Get current Discover Weekly tracks
    # tracks_api_endpoint = '{}/users/{}/playlists/{}/tracks'.format(SPOTIFY_API_URL, user_id, playlist_id)

    playlist_name = 'Discoverfy ({})'.format(arrow.utcnow().format('MM-DD-YYYY HH:mm:ss'))
    post_body = {
        'name': playlist_name,
        'public': False
    }

    if user['playlist_setting'] == 'weekly' or user['playlist_setting'] == 'hybrid':

        # Save tracks to a new playlist
        playlist_api_endpoint = '{}/users/{}/playlists'.format(SPOTIFY_API_URL, user_id)
        playlist_response = requests.post(playlist_api_endpoint, data=json.dumps(post_body), headers=headers)
        playlist_data = json.loads(playlist_response.text)
        playlist_id = playlist_data['id']

    elif user['playlist_setting'] == 'global':
        # create global playlist if it does not exist
        if user['global_playlist_id'] is None:
            post_body['name'] = 'Discoverfy Global'
            playlist_api_endpoint = '{}/users/{}/playlists'.format(SPOTIFY_API_URL, user_id)
            playlist_response = requests.post(playlist_api_endpoint, data=json.dumps(post_body), headers=headers)
            playlist_data = json.loads(playlist_response.text)

            #add global playlist to database
            database = discoverfy.model.get_db()
            cursor = database.cursor()

            cursor.execute('''
                   UPDATE users
                   SET global_playlist_id = "{}"
                   WHERE user_id = "{}"
                   '''.format(playlist_data['id'], user_id))
            playlist_id = playlist_data['id']
        else:
            playlist_id = user['global_playlist_id']

    playlist_tracks_api_endpoint = '{}/users/{}/playlists/{}/tracks'.format(SPOTIFY_API_URL, user_id, playlist_id)

    post_body = {
        'uris': track_uris
    }

    playlist_response = requests.post(playlist_tracks_api_endpoint, data=json.dumps(post_body), headers=headers)

@discoverfy.app.route('/settings/', methods=['GET', 'POST'])
def show_settings():
    if 'username' in session:
        user_id=session['username']
    else:
        redirect(url_for('show_index'))

    if request.method == 'POST': #update user settings
        print (request.form['playlist_setting'])
        print (request.form['hybrid_setting'])
        print (user_id)

        database = discoverfy.model.get_db()
        cursor = database.cursor()

        cursor.execute('''
                   UPDATE users
                   SET playlist_setting = "{}", hybrid_num_weeks = {}
                   WHERE user_id = "{}"
                   '''.format(request.form['playlist_setting'], request.form['hybrid_setting'], user_id))

        # FOR TESTING
        cursor1 = database.cursor()
        cursor.execute('''
                       SELECT *
                       FROM users
                       ''')
        result = cursor.fetchall()

        for i in result:
            print(i)

        # END FOR TESTING

    """Display /settings/ route."""
    return render_template('settings.html')
