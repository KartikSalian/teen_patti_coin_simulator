from flask import Flask, render_template, request, redirect, url_for, session, flash
import random
import string
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this in production!

# Use eventlet or threading mode depending on your deployment
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')


def generate_room_code(length=5):
    """Generate a unique room code."""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        if code not in rooms:
            return code


# In-memory storage for rooms and players
rooms = {}


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/create_room', methods=['GET', 'POST'])
def create_room():
    if request.method == 'POST':
        host_nickname = request.form['host_nickname'].strip()
        room_name = request.form['room_name'].strip()
        max_players = int(request.form['max_players'])
        starting_chips = int(request.form['starting_chips'])
        num_rounds = int(request.form['num_rounds'])

        if max_players < 2 or max_players > 10:
            flash('Max players must be between 2 and 10.', 'danger')
            return redirect(url_for('create_room'))

        room_code = generate_room_code()

        # Create room with host as first player
        rooms[room_code] = {
            'name': room_name,
            'max_players': max_players,
            'starting_chips': starting_chips,
            'num_rounds': num_rounds,
            'players': {
                host_nickname: {
                    'chips': starting_chips,
                    'ready': False,
                    'status': 'blind',  # 'blind' or 'seen'
                    'order': 0  # turn order index (0 is host)
                }
            },
            'host': host_nickname,
            'game_started': False,
            'turn_index': 0,  # index of the current player turn
            'player_order': [host_nickname],  # list of player nicknames in turn order (anticlockwise)
        }

        session['room_code'] = room_code
        session['nickname'] = host_nickname

        return redirect(url_for('lobby', room_code=room_code))

    return render_template('create_room.html')


@app.route('/join_room', methods=['GET', 'POST'])
def join_room_route():
    if request.method == 'POST':
        room_code = request.form['room_code'].strip().upper()
        nickname = request.form['nickname'].strip()

        if room_code not in rooms:
            flash('Room code not found.', 'danger')
            return redirect(url_for('join_room_route'))

        room = rooms[room_code]

        if room['game_started']:
            flash('Game already started. Cannot join now.', 'danger')
            return redirect(url_for('join_room_route'))

        if len(room['players']) >= room['max_players']:
            flash('Room is full.', 'danger')
            return redirect(url_for('join_room_route'))

        if nickname in room['players']:
            flash('Nickname already taken in this room.', 'danger')
            return redirect(url_for('join_room_route'))

        # Add player to room, assign order after current players
        order = len(room['players'])
        room['players'][nickname] = {
            'chips': room['starting_chips'],
            'ready': False,
            'status': 'blind',
            'order': order
        }
        room['player_order'].append(nickname)

        session['room_code'] = room_code
        session['nickname'] = nickname

        return redirect(url_for('lobby', room_code=room_code))

    return render_template('join_room.html')


@app.route('/room/<room_code>/lobby', methods=['GET', 'POST'])
def lobby(room_code):
    if room_code not in rooms:
        flash('Room not found.', 'danger')
        return redirect(url_for('home'))

    room = rooms[room_code]
    nickname = session.get('nickname')

    if not nickname or nickname not in room['players']:
        flash('You are not part of this room.', 'danger')
        return redirect(url_for('join_room_route'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'toggle_ready':
            player = room['players'][nickname]
            player['ready'] = not player['ready']

        elif action == 'start_game':
            if nickname == room['host']:
                ready_count = sum(1 for p in room['players'].values() if p['ready'])
                if ready_count >= 2:
                    room['game_started'] = True
                    room['turn_index'] = 0  # reset turn index at game start

                    # Broadcast to all players in room that game started
                    socketio.emit('game_started', {'room_code': room_code}, room=room_code)

                    return redirect(url_for('game', room_code=room_code))
                else:
                    flash('At least 2 players must be ready to start.', 'warning')
            else:
                flash('Only the host can start the game.', 'warning')

    ready_count = sum(1 for p in room['players'].values() if p['ready'])

    return render_template(
        'lobby.html',
        room=room,
        nickname=nickname,
        room_code=room_code,
        ready_count=ready_count
    )


@app.route('/game/<room_code>')
def game(room_code):
    if room_code not in rooms or not rooms[room_code].get('game_started'):
        flash('Game has not started yet or room does not exist.', 'danger')
        return redirect(url_for('lobby', room_code=room_code))

    room = rooms[room_code]
    nickname = session.get('nickname')

    if not nickname or nickname not in room['players']:
        flash('You are not part of this room.', 'danger')
        return redirect(url_for('join_room_route'))

    return render_template('game.html', room=room, nickname=nickname, room_code=room_code)


@app.route('/game/<room_code>/update_chips', methods=['POST'])
def update_chips(room_code):
    if room_code not in rooms:
        flash('Room not found.', 'danger')
        return redirect(url_for('home'))

    room = rooms[room_code]
    nickname = session.get('nickname')

    if nickname != room['host']:
        flash('Only the host can update chips.', 'danger')
        return redirect(url_for('game', room_code=room_code))

    player_name = request.form.get('player_name')
    chip_delta = int(request.form.get('chip_delta', 0))

    if player_name in room['players']:
        room['players'][player_name]['chips'] += chip_delta
        flash(f"{player_name}'s chips updated by {chip_delta}.", 'success')
    else:
        flash('Player not found in room.', 'danger')

    return redirect(url_for('game', room_code=room_code))


@app.route('/leave_room')
def leave_room_route():
    session.pop('room_code', None)
    session.pop('nickname', None)
    return redirect(url_for('home'))


# === SocketIO Events === #

@socketio.on("join_game")
def on_join_game(data):
    room_code = data.get("room")
    nickname = data.get("nickname")

    if room_code not in rooms or nickname not in rooms[room_code]['players']:
        return  # invalid join request

    join_room(room_code)
    print(f"{nickname} joined room {room_code}")

    emit("player_joined", {"nickname": nickname}, room=room_code)


@socketio.on("player_action")
def on_player_action(data):
    room_code = data.get("room")
    nickname = data.get("nickname")
    action = data.get("action")
    payload = data.get("payload")  # Optional extra data

    if room_code not in rooms or nickname not in rooms[room_code]['players']:
        return

    print(f"Player {nickname} in room {room_code} performed action: {action} with payload: {payload}")

    # TODO: Implement your game logic here (e.g., Blind, Chaal, Raise, Pack, Side Show, Show, Toggle Seen)
    # After processing action, broadcast updated game state or event
    emit("game_update", {
        "message": f"{nickname} performed action {action}",
        "nickname": nickname,
        "action": action,
        "payload": payload
    }, room=room_code)


if __name__ == '__main__':
    socketio.run(app, debug=True)
