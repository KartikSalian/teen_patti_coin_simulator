from flask import Flask, render_template, request, redirect, url_for, session, flash
import random
import string
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this!
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')  # or 'threading'
def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# In-memory storage
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
                    'ready': False
                }
            },
            'host': host_nickname,
            'game_started': False
        }

        # Save in session
        session['room_code'] = room_code
        session['nickname'] = host_nickname

        # Redirect directly to lobby
        return redirect(url_for('lobby', room_code=room_code))

    return render_template('create_room.html')



@app.route('/join_room', methods=['GET', 'POST'])
def join_room_route():
    if request.method == 'POST':
        room_code = request.form['room_code']
        nickname = request.form['nickname'].strip()
        if room_code not in rooms:
            flash('Room code not found.', 'danger')
            return redirect(url_for('join_room_route'))

        room = rooms[room_code]
        if len(room['players']) >= room['max_players']:
            flash('Room full.', 'danger')
            return redirect(url_for('join_room_route'))

        # Add player if nickname unique
        if nickname in room['players']:
            flash('Nickname taken in this room.', 'danger')
            return redirect(url_for('join_room_route'))

        # Add player to room
        room['players'][nickname] = {
            'chips': room['starting_chips'],
            'ready': False
        }
        session['room_code'] = room_code
        session['nickname'] = nickname

        # Set host if first player
        if not room['host']:
            room['host'] = nickname

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

                    # Emit 'game_started' event to all players in the room
                    socketio.emit('game_started', {'room_code': room_code}, room=room_code)

                    return redirect(url_for('game', room_code=room_code))
                else:
                    flash('At least 2 players must be ready to start.', 'warning')
            else:
                flash('Only the host can start the game.', 'warning')

    ready_count = sum(1 for p in room['players'].values() if p['ready'])

    return render_template('lobby.html', room=room, nickname=nickname, room_code=room_code, ready_count=ready_count)


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


@app.route('/game/<room_code>/chips', methods=['POST'])
def manage_chips(room_code):
    if room_code not in rooms:
        flash('Room not found.', 'danger')
        return redirect(url_for('home'))

    room = rooms[room_code]
    nickname = session.get('nickname')

    if nickname != room['host']:
        flash('Only the host can update chips.', 'danger')
        return redirect(url_for('game', room_code=room_code))

    player_name = request.form['player_name']
    chip_delta = int(request.form['chip_delta'])

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


@socketio.on("join_game")
def handle_join_game(data):
    room = data["room"]
    nickname = data["nickname"]
    join_room(room)
    print(f"{nickname} joined room {room}")

    emit("player_joined", {"nickname": nickname}, room=room)


@socketio.on("submit_answer")
def handle_submit_answer(data):
    room = data["room"]
    nickname = data["nickname"]
    answer = data["answer"]

    print(f"{nickname} answered: {answer}")

    # Placeholder for actual game logic
    emit("game_update", {
        "message": f"{nickname} submitted an answer: {answer}",
        "nickname": nickname,
        "answer": answer
    }, room=room)


if __name__ == '__main__':
    socketio.run(app, debug=True)