import robot_race_functions as rr
from collections import deque, Counter, namedtuple
from time import time, sleep

maze_file_name = 'maze_data_1.csv'
seconds_between_turns = 0.3
max_turns = 35

maze_data = rr.read_maze(maze_file_name)
rr.print_maze(maze_data)
walls, goal, bots = rr.process_maze_init(maze_data)

robot_moves = deque()
num_of_turns = 0

while not rr.is_race_over(bots) and num_of_turns < max_turns:
    for bot in bots:
        if not bot.has_finished:
            bot_move = rr.compute_robot_logic(walls, goal, bot)
            robot_moves.append(bot_move)
    num_of_turns += 1

move_count = Counter([move[0] for move in robot_moves])

collision_count = Counter([move[0] for move in robot_moves if move[2]])

BotScoreData = namedtuple('BotScoreData', ['name', 'num_moves', 'num_collisions', 'score'])

bot_scores = []
for bot in bots:
    num_moves = move_count[bot.name]
    num_collisions = collision_count[bot.name]
    score = num_moves + num_collisions
    bot_scores.append(BotScoreData(bot.name, num_moves, num_collisions, score))

bot_data = {bot.name: bot for bot in bots}

while len(robot_moves) > 0:
    move = robot_moves.popleft()
    bot_name, action, _ = move
    bot = bot_data[bot_name]
    bot.process_move(action)
    rr.update_maze_characters(maze_data, bots)
    rr.print_maze(maze_data)
    sleep(seconds_between_turns - time() % seconds_between_turns)

rr.print_results(bot_scores)
