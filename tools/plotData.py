import sys
sys.path.append("..") # Adds higher directory to python modules path.

import numpy as np
import matplotlib.pyplot as plt
import torch
from torch.autograd import Variable
import time

from cassie import CassieEnv

import argparse
import pickle

parser = argparse.ArgumentParser()
parser.add_argument("--path", type=str, default=None, help="path to folder containing policy and run details")
args = parser.parse_args()
run_args = pickle.load(open(args.path + "experiment.pkl", "rb"))

RUN_NAME = run_args.run_name if run_args.run_name != None else "plot"

# RUN_NAME = "7b7e24-seed0"
# POLICY_PATH = "../trained_models/ppo/Cassie-v0/" + RUN_NAME + "/actor.pt"

# Load environment and policy
# env_fn = partial(CassieEnv_speed_no_delta_neutral_foot, "walking", clock_based=True, state_est=True)
cassie_env = CassieEnv(traj=run_args.traj, state_est=run_args.state_est, dynamics_randomization=run_args.dyn_random, clock_based=run_args.clock_based, history=run_args.history)
policy = torch.load(args.path + "actor.pt")

def avg_pols(policies, state):
    total_act = np.zeros(10)
    for policy in policies:
        action = policy.forward(torch.Tensor(state), True).detach().numpy()
        total_act += action
    return total_act / len(policies)

obs_dim = cassie_env.observation_space.shape[0] # TODO: could make obs and ac space static properties
action_dim = cassie_env.action_space.shape[0]

do_multi = False
no_delta = cassie_env.no_delta
limittargs = False
lininterp = False
offset = cassie_env.offset

policies = []
if do_multi:
    # for i in range(5, 12):
    #     policy = torch.load("./trained_models/regular_spring"+str(i)+".pt")
    #     policy.eval()
    #     policies.append(policy)
    # policy = torch.load("./trained_models/Normal.pt")
    # policy.eval()
    # policies.append(policy)
    # policy = torch.load("./trained_models/stiff_StateEst_step.pt")
    # policy.eval()
    # policies.append(policy)
    for i in [1, 2, 3, 5]:
        policy = torch.load("./trained_models/stiff_spring/stiff_StateEst_speed{}.pt".format(i))
        policies.append(policy)

num_steps = 100
pre_steps = 300
simrate = 60
torques = np.zeros((num_steps*simrate, 10))
GRFs = np.zeros((num_steps*simrate, 2))
targets = np.zeros((num_steps*simrate, 10))
heights = np.zeros(num_steps*simrate)
speeds = np.zeros(num_steps*simrate)
foot_pos = np.zeros((num_steps*simrate, 6))
mj_foot_pos = np.zeros((num_steps*simrate, 6))
foot_vel = np.zeros((num_steps*simrate, 6))
actions = np.zeros((num_steps*simrate, 10))
pelaccel = np.zeros(num_steps*simrate)
pelheight = np.zeros(num_steps*simrate)
act_diff = np.zeros(num_steps*simrate)
actuated_pos = np.zeros((num_steps*simrate, 10))
actuated_vel = np.zeros((num_steps*simrate, 10))
prev_action = None
pos_idx = [7, 8, 9, 14, 20, 21, 22, 23, 28, 34]
vel_idx = [6, 7, 8, 12, 18, 19, 20, 21, 25, 31]
# Execute policy and save torques
with torch.no_grad():
    state = cassie_env.reset_for_test()
    cassie_env.speed = 0
    # cassie_env.side_speed = .2
    for i in range(pre_steps):
        if not do_multi:
            action = policy.forward(torch.Tensor(state), deterministic=True).detach().numpy()
            state, reward, done, _ = cassie_env.step(action)
        else:
            action = avg_pols(policies, state)
            state, reward, done, _ = cassie_env.step(action)
        state = torch.Tensor(state)
    for i in range(num_steps):
        if not do_multi:
            action = policy.forward(torch.Tensor(state), deterministic=True).detach().numpy()
        else:
            action = avg_pols(policies, state)
            # state, reward, done, _ = cassie_env.step(action)
        # targets[i, :] = action
        lin_steps = int(60 * 3/4)  # Number of steps to interpolate over. Should be between 0 and self.simrate
        alpha = 1 / lin_steps
        for j in range(simrate):
            if no_delta:
                target = action + offset
            else:
                ref_pos, ref_vel = cassie_env.get_ref_state(cassie_env.phase + cassie_env.phase_add)
                target = action + ref_pos[cassie_env.pos_idx]
            if limittargs:
                h = 0.0001
                Tf = 1.0 / 300.0
                alpha = h / (Tf + h)
                real_action = (1-alpha)*cassie_env.prev_action + alpha*target
                actions[i*simrate+j, :] = real_action
            elif lininterp:  
                if prev_action is not None:
                    real_action = (1-alpha)*prev_action + alpha*action
                    if alpha < 1:
                        alpha += 1 / lin_steps
                    else:
                        alpha = 1
                else:
                    real_action = action
                actions[i*simrate+j, :] = real_action
            else:
                real_action = action
                actions[i*simrate+j, :] = action
            targets[i*simrate+j, :] = target
            # print(target)

            cassie_env.step_simulation(real_action)
            curr_qpos = cassie_env.sim.qpos()
            curr_qvel = cassie_env.sim.qvel()
            torques[i*simrate+j, :] = cassie_env.cassie_state.motor.torque[:]
            GRFs[i*simrate+j, :] = cassie_env.sim.get_foot_forces()
            heights[i*simrate+j] = curr_qpos[2]
            speeds[i*simrate+j] = cassie_env.sim.qvel()[0]
            curr_foot = np.concatenate((cassie_env.cassie_state.leftFoot.position, cassie_env.cassie_state.rightFoot.position))
            curr_foot += np.concatenate((cassie_env.cassie_state.pelvis.position, cassie_env.cassie_state.pelvis.position))
            mj_foot = np.zeros(6)
            cassie_env.sim.foot_pos(mj_foot)
            mj_foot_pos[i*simrate+j, :] = mj_foot
            foot_pos[i*simrate+j, :] = curr_foot
            # print("left foot height: ", cassie_env.cassie_state.leftFoot.position[2])
            foot_vel[i*simrate+j, :] = np.concatenate((cassie_env.cassie_state.leftFoot.footTranslationalVelocity, cassie_env.cassie_state.rightFoot.footTranslationalVelocity))
            pelaccel[i*simrate+j] = cassie_env.cassie_state.pelvis.translationalAcceleration[2]#np.linalg.norm(cassie_env.cassie_state.pelvis.translationalAcceleration)
            pelheight[i*simrate+j] = cassie_env.cassie_state.pelvis.position[2]
            actuated_pos[i*simrate+j, :] = [curr_qpos[k] for k in pos_idx]
            actuated_vel[i*simrate+j, :] = [curr_qvel[k] for k in vel_idx]
            if prev_action is not None:
                act_diff[i*simrate+j] = np.linalg.norm(action - prev_action)
            else:
                act_diff[i*simrate+j] = 0
        prev_action = action

        cassie_env.time  += 1
        cassie_env.phase += cassie_env.phase_add

        if cassie_env.phase > cassie_env.phaselen:
            cassie_env.phase = 0
            cassie_env.counter += 1

        state = cassie_env.get_full_state()

# Graph torque data
fig, ax = plt.subplots(2, 5, figsize=(15, 5))
t = np.linspace(0, num_steps-1, num_steps*simrate)
titles = ["Hip Roll", "Hip Yaw", "Hip Pitch", "Knee", "Foot"]
ax[0][0].set_ylabel("Torque")
ax[1][0].set_ylabel("Torque")
for i in range(5):
    ax[0][i].plot(t, torques[:, i])
    ax[0][i].set_title("Left " + titles[i])
    ax[1][i].plot(t, torques[:, i+5])
    ax[1][i].set_title("Right " + titles[i])
    ax[1][i].set_xlabel("Timesteps (0.03 sec)")

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_torques.png")

# Graph GRF data
fig, ax = plt.subplots(2, figsize=(10, 5))
t = np.linspace(0, num_steps-1, num_steps*simrate)
ax[0].set_ylabel("GRFs")

ax[0].plot(t, GRFs[:, 0])
ax[0].set_title("Left Foot")
ax[0].set_xlabel("Timesteps (0.03 sec)")
ax[1].plot(t, GRFs[:, 1])
ax[1].set_title("Right Foot")
ax[1].set_xlabel("Timesteps (0.03 sec)")

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_GRFs.png")

# Graph PD target data
fig, ax = plt.subplots(2, 5, figsize=(15, 5))
t = np.linspace(0, num_steps-1, num_steps*simrate)
titles = ["Hip Roll", "Hip Yaw", "Hip Pitch", "Knee", "Foot"]
ax[0][0].set_ylabel("PD Target")
ax[1][0].set_ylabel("PD Target")
for i in range(5):
    ax[0][i].plot(t, targets[:, i])
    ax[0][i].set_title("Left " + titles[i])
    ax[1][i].plot(t, targets[:, i+5])
    ax[1][i].set_title("Right " + titles[i])
    ax[1][i].set_xlabel("Timesteps (0.03 sec)")

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_targets.png")

# Graph action data
fig, ax = plt.subplots(2, 5, figsize=(15, 5))
t = np.linspace(0, num_steps-1, num_steps*simrate)
titles = ["Hip Roll", "Hip Yaw", "Hip Pitch", "Knee", "Foot"]
ax[0][0].set_ylabel("Action")
ax[1][0].set_ylabel("Action")
for i in range(5):
    ax[0][i].plot(t, actions[:, i])
    ax[0][i].set_title("Left " + titles[i])
    ax[1][i].plot(t, actions[:, i+5])
    ax[1][i].set_title("Right " + titles[i])
    ax[1][i].set_xlabel("Timesteps (0.03 sec)")

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_actions.png")

# Graph state data
fig, ax = plt.subplots(2, 2, figsize=(10, 5))
t = np.linspace(0, num_steps-1, num_steps*simrate)
ax[0][0].set_ylabel("norm")
ax[0][0].plot(t, pelaccel[:])
ax[0][0].set_title("Pel Z Accel")
ax[0][1].set_ylabel("m/s")
ax[0][1].plot(t, np.linalg.norm(torques, axis=1))
ax[0][1].set_title("Torque Norm")
titles = ["Left", "Right"]
for i in range(2):
    ax[1][i].plot(t, mj_foot_pos[:, 3*i+2])
    ax[1][i].set_title(titles[i] + " Foot")
    ax[1][i].set_xlabel("Timesteps (0.03 sec)")

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_state.png")

# Graph feet qpos data
fig, ax = plt.subplots(5, 2, figsize=(12, 6), sharex=True, sharey='row')
t = np.linspace(0, num_steps*60*0.0005, num_steps*simrate)
ax[3][0].set_xlabel("Time (sec)")
ax[3][1].set_xlabel("Time (sec)")
sides = ["Left", "Right"]
titles = [" Foot Z Position", " Foot X Velocity", " Foot Y Velocity", " Foot Z Velocity"]
for i in range(2):
    # ax[0][i].plot(t, foot)
    ax[0][i].plot(t, foot_pos[:, 3*i+2])
    ax[0][i].set_title(sides[i] + titles[0])
    ax[0][i].set_ylabel("Z Position (m)")
    ax[1][i].plot(t, mj_foot_pos[:, 3*i+2])
    ax[1][i].set_title(sides[i] + " mj foot z pos")
    ax[1][i].set_ylabel("Z Position (m)")
    for j in range(3):
        ax[j+2][i].plot(t, foot_vel[:, 3*i+j])
        ax[j+2][i].set_title(sides[i] + titles[j+1])
        ax[j+2][i].set_ylabel("Velocity (m/s)")    

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_feet.png")

# Graph phase portrait for actuated joints
fig, ax = plt.subplots(1, 5, figsize=(15, 4))
titles = ["Hip Roll", "Hip Yaw", "Hip Pitch", "Knee", "Foot"]
ax[0].set_ylabel("Velocity")
# ax[1][0].set_ylabel("Velocity")
for i in range(5):
    ax[i].plot(actuated_pos[:, i], actuated_vel[:, i])
    ax[i].plot(actuated_pos[:, i+5], actuated_vel[:, i+5])
    ax[i].set_title(titles[i])
    # ax[1][i].plot(actuated_pos[:, i+5], actuated_vel[:, i+5])
    # ax[1][i].set_title("Right " + titles[i])
    ax[i].set_xlabel("Angle")

plt.tight_layout()
plt.savefig(args.path+RUN_NAME+"_phaseportrait.png")

# Misc Plotting
fig, ax = plt.subplots()
t = np.linspace(0, num_steps-1, num_steps*simrate)
# ax.set_ylabel("norm")
# ax.set_title("Action - Prev Action Norm")
# ax.plot(t, act_diff)
ax.set_ylabel("Height (m)")
ax.set_title("Pelvis Height")
ax.plot(t, pelheight)
plt.savefig(args.path+RUN_NAME+"_misc.png")