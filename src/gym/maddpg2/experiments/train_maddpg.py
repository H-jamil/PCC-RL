# python3 train_maddpg.py --max-episode-len 100 --save-rate 100 --num-agents 2 --log-dir 210 --num-episodes 10000
# python3 make_graph.py --num-agents 1 --dump-rate 1000 --save-rate 100 --in-out 1 1 --log-range 1 -1 --criteria reward
# source ~/.bashrc
# trainers[1].p_debug['target_act'](obs_n[0][None])
# python3 train_maddpg.py --scenario simple_spread

# add to PATHONPATH
import os, sys
from pathlib import Path
cpath = Path(os.getcwd())
sys.path.append(str(cpath.parents[2]))

import argparse
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.python import debug as tf_debug
import time
import pickle
import pdb


from maddpg2.maddpg2.trainer.maddpg import MADDPGAgentTrainer, RandomTrainer
import tensorflow.contrib.layers as layers
from network_sim import SimulatedMultAgentNetworkEnv
import network_sim
import maddpg2.maddpg2.common.tf_util as U
from tensorflow.python.tools import inspect_checkpoint as chkp

def parse_args():
    parser = argparse.ArgumentParser("Reinforcement Learning experiments for multiagent environments")
    # Environment
    parser.add_argument("--num-agents", type=int, default=1, help="number of good agents")
    parser.add_argument("--log-dir", type=str, default="0", help="directory in which log files are saved")

    parser.add_argument("--scenario", type=str, default="simple", help="name of the scenario script")
    parser.add_argument("--max-episode-len", type=int, default=25, help="maximum episode length")
    parser.add_argument("--num-episodes", type=int, default=60000, help="number of episodes")
    parser.add_argument("--num-adversaries", type=int, default=0, help="number of adversaries")
    parser.add_argument("--good-policy", type=str, default="maddpg", help="policy for good agents")
    parser.add_argument("--adv-policy", type=str, default="maddpg", help="policy of adversaries")
    # Core training parameters
    parser.add_argument("--lr", type=float, default=1e-2, help="learning rate for Adam optimizer")
    parser.add_argument("--gamma", type=float, default=0.95, help="discount factor")
    parser.add_argument("--batch-size", type=int, default=1024, help="number of episodes to optimize at the same time")
    parser.add_argument("--num-units", type=int, default=64, help="number of units in the mlp")
    # Checkpointing

    parser.add_argument("--exp-name", type=str, default=None, help="name of the experiment")
    parser.add_argument("--save-dir", type=str, default="/tmp/policy/", help="directory in which training state and model should be saved")
    parser.add_argument("--save-rate", type=int, default=1000, help="save model once every time this many episodes are completed")
    parser.add_argument("--load-dir", type=str, default="", help="directory in which training state and model are loaded")
    # Evaluation
    parser.add_argument("--restore", action="store_true", default=False)
    parser.add_argument("--display", action="store_true", default=False)
    parser.add_argument("--benchmark", action="store_true", default=False)
    parser.add_argument("--benchmark-iters", type=int, default=100000, help="number of iterations run for benchmarking")
    parser.add_argument("--benchmark-dir", type=str, default="./benchmark_files/", help="directory where benchmark data is saved")
    parser.add_argument("--plots-dir", type=str, default="./learning_curves/", help="directory where plot data is saved")
    return parser.parse_args()

def weight_variable(shape):
    """Create a weight variable with appropriate initialization."""
    #return tf.truncated_normal(shape, stddev=0.1)
    initial = tf.truncated_normal(shape, stddev=0.1)
    #print(tf.Variable(initial))
    #return tf.Variable(initial)
    #print(tf.compat.v1.get_variable(name='weights', initializer = initial))
    return tf.compat.v1.get_variable(name='weights', initializer = initial)

def bias_variable(shape):
    """Create a bias variable with appropriate initialization."""
    initial = tf.constant(0.1, shape=shape)
    #return tf.Variable(initial)
    return tf.compat.v1.get_variable(name='bias', initializer=initial)

def variable_summaries(var):
    """Attach a lot of summaries to a Tensor (for TensorBoard visualization)."""
    with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.compat.v1.summary.scalar('mean', mean)
        with tf.name_scope('stddev'):
            stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
        tf.compat.v1.summary.scalar('stddev', stddev)
        tf.compat.v1.summary.scalar('max', tf.reduce_max(var))
        tf.compat.v1.summary.scalar('min', tf.reduce_min(var))
        tf.compat.v1.summary.histogram('histogram', var)

def nn_layer(input_tensor, input_dim, output_dim, layer_name, act=tf.nn.relu):
    """Reusable code for making a simple neural net layer.
    It does a matrix multiply, bias add, and then uses ReLU to nonlinearize.
    It also sets up name scoping so that the resultant graph is easy to read,
    and adds a number of summary ops."""
    # Adding a name scope ensures logical grouping of the layers in the graph.
    with tf.variable_scope(layer_name):
        # This Variable will hold the state of the weights for the layer
        #with tf.name_scope('weights'):
        weights = weight_variable([input_dim, output_dim])
        variable_summaries(weights)
        #with tf.name_scope('biases'):
        biases = bias_variable([output_dim])
        variable_summaries(biases)
        with tf.name_scope('Wx_plus_b'):
            preactivate = tf.einsum('ij,jk->ik', input_tensor, weights) + biases
            tf.compat.v1.summary.histogram('pre_activations', preactivate)
        activations = act(preactivate, name='activation')
        tf.compat.v1.summary.histogram('activations', activations)
        return activations

''' add batch_normalization '''
def mlp_model(input, num_outputs, scope, reuse=False, num_units=64, rnn_cell=None):
    # This model takes as input an observation and returns values of all actions
    with tf.variable_scope(scope, reuse=reuse):
        out = input
        out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.relu, normalizer_fn=tf.contrib.layers.batch_norm)
        out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.relu, normalizer_fn=tf.contrib.layers.batch_norm)

        #out = layers.fully_connected(out, num_outputs=num_outputs, activation_fn=None)
        out = layers.fully_connected(out, num_outputs=num_outputs, activation_fn=None, normalizer_fn=tf.contrib.layers.batch_norm)

        #out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.softmax)
        #out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.softmax)
        #out = layers.fully_connected(out, num_outputs=num_outputs, activation_fn=None, normalizer_fn=None)
        return out

''' original achitecture '''
def mlp_model2(input, num_outputs, scope, reuse=False, num_units=64, rnn_cell=None):
    # This model takes as input an observation and returns values of all actions
    with tf.variable_scope(scope, reuse=reuse):
        print("mlp_m2")
        out = input
        print(out)
        out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.relu)
        print(out)
        out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.relu)
        print(out)
        out = layers.fully_connected(out, num_outputs=num_outputs, activation_fn=None)
        print(out)
        return out

''' tensorboard equivalent achitecture '''
def mlp_model3(input, num_outputs, scope, reuse=False, num_units=64, rnn_cell=None):
    # This model takes as input an observation and returns values of all actions
    # scope: p_func, q_func, target_q_func
    with tf.variable_scope(scope, reuse=reuse):
        print(input)
        hidden1 = nn_layer(input, input.shape[1].value, num_units, 'layer1')
        print(hidden1)
        hidden2 = nn_layer(hidden1, hidden1.shape[1].value, num_units, 'layer2')
        print(hidden2)
        out = nn_layer(hidden2, hidden1.shape[1].value, num_outputs, 'layer3', act=tf.identity)
        print(out)
        return out

''' run network_sim.py '''
def make_env(arglist):
    env = SimulatedMultAgentNetworkEnv(arglist)
    return env

''' run mpe (multi-particle env) '''
def make_env2(scenario_name, arglist, benchmark=False):
    from maddpg2.mpe.multiagent.environment import MultiAgentEnv
    import maddpg2.mpe.multiagent.scenarios as scenarios
    # load scenario from script
    scenario = scenarios.load(scenario_name + ".py").Scenario()
    # create world
    world = scenario.make_world()
    # create multiagent environment
    if benchmark:
        env = MultiAgentEnv(world, scenario.reset_world, scenario.reward, scenario.observation, scenario.benchmark_data)
    else:
        env = MultiAgentEnv(world, scenario.reset_world, scenario.reward, scenario.observation)
    return env

def get_trainers(env, num_adversaries, obs_shape_n, arglist):
    trainers = []
    model = mlp_model2
    #trainer = RandomTrainer
    trainer = MADDPGAgentTrainer
    for i in range(num_adversaries):
        trainers.append(trainer(
            "agent_%d" % i, model, obs_shape_n, env.action_space, i, arglist,
            local_q_func=(arglist.adv_policy=='ddpg')))
    for i in range(num_adversaries, env.n):
        trainers.append(trainer(
            "agent_%d" % i, model, obs_shape_n, env.action_space, i, arglist,
            local_q_func=(arglist.good_policy=='ddpg')))
    return trainers


def train(arglist):
    with U.single_threaded_session() as sess:
        # Create environment
        env = make_env(arglist)
        #env = make_env2(arglist.scenario, arglist, arglist.benchmark)

        # Create agent trainers
        obs_shape_n = [env.observation_space[i].shape for i in range(env.n)]
        num_adversaries = min(env.n, arglist.num_adversaries)
        trainers = get_trainers(env, num_adversaries, obs_shape_n, arglist)
        print('Using good policy {} and adv policy {}'.format(arglist.good_policy, arglist.adv_policy))

        # Initialize
        U.initialize()
        saver = tf.train.Saver()

        # Load previous results, if necessary
        if arglist.load_dir == "":
            arglist.load_dir = arglist.save_dir
        if arglist.display or arglist.restore or arglist.benchmark:
            print('Loading previous state...')
            U.load_state(arglist.load_dir)
            #saver.restore(sess, "model_episode_1000.ckpt")
            #tf.print()

        episode_rewards = [0.0]  # sum of rewards for all agents
        agent_rewards = [[0.0] for _ in range(env.n)]  # individual agent reward
        final_ep_rewards = []  # sum of rewards for training curve
        final_ep_ag_rewards = []  # agent rewards for training curve
        agent_info = [[[]]]  # placeholder for benchmarking info


        obs_n = env.reset()
        episode_step = 0
        train_step = 0
        t_start = time.time()
        #best_rew = 0
        #best_params = None
        '''
        merged = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter(FLAGS.log_dir + '/train', sess.graph)
        test_writer = tf.summary.FileWriter(FLAGS.log_dir + '/test')
        tf.global_variables_initializer().run()'''

        #merged = tf.compat.v1.summary.merge_all()
        #print(merged)
        #summary_writer = tf.compat.v1.summary.FileWriter('/tmp' + '/train', sess.graph)
        tf.global_variables_initializer().run()

        print('Starting iterations...')
        # tf.add_check_numerics_ops()
        while True:
            # get action

            action_n = [agent.action(obs) for agent, obs in zip(trainers,obs_n)]
            #print(obs_n)
            #print(action_n)
            # got nan?

            # environment step

            # each new_obs should have global view??
            new_obs_n, rew_n, done_n, info_n = env.step(action_n)

            #if(np.sum(rew_n) > best_rew):
            #    best_rew = np.sum(rew_n)
            #    best_params = [agent.parameters for agent in trainers]

            boo1 = np.any(pd.isnull(new_obs_n))
            boo2 = np.any(pd.isnull(rew_n))
            boo3 = np.any(pd.isnull(action_n))
            if boo1 == True or boo2 == True or boo3 == True:
                save_path = saver.save(sess, "model_nan.ckpt")
                chkp.print_tensors_in_checkpoint_file("model_nan.ckpt", tensor_name='', all_tensors=True)
                pdb.set_trace() # Break into debugger to look around
                print(new_obs_n)
                print(rew_n)
                print(action_n)

            episode_step += 1
            done = all(done_n)
            terminal = (episode_step >= arglist.max_episode_len)
            # collect experience
            for i, agent in enumerate(trainers):
                agent.experience(obs_n[i], action_n[i], rew_n[i], new_obs_n[i], done_n[i], terminal)
            obs_n = new_obs_n

            for i, rew in enumerate(rew_n):
                episode_rewards[-1] += rew
                agent_rewards[i][-1] += rew

            if done or terminal:
                obs_n = env.reset()
                [agent.reset() for agent in trainers]
                episode_step = 0
                episode_rewards.append(0)
                for a in agent_rewards:
                    a.append(0)
                agent_info.append([[]])

            # increment global step counter
            train_step += 1

            # for benchmarking learned policies
            if arglist.benchmark:
                for i, info in enumerate(info_n):
                    agent_info[-1][i].append(info_n['n'])
                if train_step > arglist.benchmark_iters and (done or terminal):
                    file_name = arglist.benchmark_dir + arglist.exp_name + '.pkl'
                    print('Finished benchmarking, now saving...')
                    with open(file_name, 'wb') as fp:
                        pickle.dump(agent_info[:-1], fp)
                    break
                continue

            # for displaying learned policies
            if arglist.display:
                time.sleep(0.1)
                env.render()
                continue

            # update all trainers, if not in display or benchmark mode
            loss = None
            for agent in trainers:
                agent.preupdate()
            for agent in trainers:
                loss = agent.update(trainers, train_step)

            #print(merged)
            #[summary_str] = sess.run([merged])
            #summary_writer.add_summary(summary_str, num_epi)

            # save model, display training output
            if terminal and (len(episode_rewards) % arglist.save_rate == 0):
                agent_epi_rew = [np.mean(rew[-arglist.save_rate:]) for rew in agent_rewards]
                mean_epi_rew = np.mean(episode_rewards[-arglist.save_rate:])
                num_epi = len(episode_rewards)

                #[summary_str] = sess.run([merged])
                #summary_writer.add_summary(summary_str, num_epi)

                root = os.getcwd()+"/tmp"
                fname = root + "/model_episode_{}.ckpt".format(len(episode_rewards))
                U.save_state(root, fname, saver=saver)
                #chkp.print_tensors_in_checkpoint_file(fname, tensor_name='', all_tensors=True)

                #if (mean_epi_rew > best_rew):
                #    best_rew = mean_epi_rew
                #    best_params = [agent.parameters for agent in trainers]

                print("steps: {}, episodes: {}, mean episode reward: {}, agent episode reward: {}, time: {}".format(
                        train_step, num_epi, mean_epi_rew, agent_epi_rew, round(time.time()-t_start, 3)))
                #print("best_reward: {}, best_params: {}".format(best_rew, best_params))

                '''else:
                    print("steps: {}, episodes: {}, mean episode reward: {}, agent episode reward: {}, time: {}".format(
                        train_step, len(episode_rewards), np.mean(episode_rewards[-arglist.save_rate:]),
                        [np.mean(rew[-arglist.save_rate:]) for rew in agent_rewards], round(time.time()-t_start, 3)))'''

                t_start = time.time()
                # Keep track of final episode reward
                final_ep_rewards.append(np.mean(episode_rewards[-arglist.save_rate:]))
                for rew in agent_rewards:
                    final_ep_ag_rewards.append(np.mean(rew[-arglist.save_rate:]))

            # saves final episode reward for plotting training curve later
            if len(episode_rewards) > arglist.num_episodes:
                rew_file_name = arglist.plots_dir + arglist.exp_name + '_rewards.pkl'
                with open(rew_file_name, 'wb') as fp:
                    pickle.dump(final_ep_rewards, fp)
                agrew_file_name = arglist.plots_dir + arglist.exp_name + '_agrewards.pkl'
                with open(agrew_file_name, 'wb') as fp:
                    pickle.dump(final_ep_ag_rewards, fp)
                print('...Finished total of {} episodes.'.format(len(episode_rewards)))
                break

if __name__ == '__main__':
    arglist = parse_args()
    train(arglist)
