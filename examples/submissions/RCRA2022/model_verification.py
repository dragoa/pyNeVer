import logging
import time
from datetime import datetime

import numpy as np
import onnx
import torch
from gym import spaces

import pynever.nodes as nodes
import pynever.strategies.conversion as conv
import pynever.strategies.verification as ver
import shared_constants

if __name__ == '__main__':

    logger_exp_stream = logging.getLogger("pynever.strategies.verification")
    logger_exp_file = logging.getLogger("exp_file")

    exp_file_handler = logging.FileHandler(f"logs/{datetime.now().strftime('%m.%d.%Y_%H.%M.%S')}-ExperimentLog.txt")
    exp_stream_handler = logging.StreamHandler()

    exp_file_handler.setLevel(logging.INFO)
    exp_stream_handler.setLevel(logging.INFO)

    logger_exp_file.addHandler(exp_file_handler)
    logger_exp_stream.addHandler(exp_stream_handler)

    logger_exp_file.setLevel(logging.INFO)
    logger_exp_stream.setLevel(logging.INFO)

    net_ids = shared_constants.id_arch_dict.keys()
    net_paths = [f"onnx_nets/{x}.onnx" for x in net_ids]

    epsilons = [0.1, 0.01]

    in_low_b = np.array([-1, -1, 0, -1, -1, -1, -1, -1, -1, -1, -1, -1])
    in_high_b = np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
    input_space = spaces.Box(low=in_low_b, high=in_high_b, dtype=np.float32)
    input_space.seed(shared_constants.SEED)

    random_input = input_space.sample()
    logger_exp_file.info(f"Input Sample:{random_input}")
    tensor_input = torch.from_numpy(random_input)

    heuristics = ["mixed"]
    num_neuron_mixed = 1

    logger_exp_file.info("NETWORK_ID, HEURISTIC, EPSILON, MIN_LB, MAX_UB, DELTA, TIME")

    for net_path in net_paths:

        net_id = net_path.replace("onnx_nets/", "").replace(".onnx", "")
        logger_exp_stream.info(f"EVALUATING NET {net_id} ({net_path})")

        onnx_net = conv.ONNXNetwork(net_id, onnx.load(net_path))
        net = conv.ONNXConverter().to_neural_network(onnx_net)
        torch_net = conv.PyTorchConverter().from_neural_network(net).pytorch_network

        numpy_output = torch_net(tensor_input).detach().numpy()

        for heuristic in heuristics:

            ver_param = []

            for node in net.nodes.values():
                if isinstance(node, nodes.ReLUNode):
                    if heuristic == "overapprox":
                        ver_param.append([0])
                    elif heuristic == "mixed":
                        ver_param.append([num_neuron_mixed])
                    elif heuristic == "complete":
                        ver_param.append([node.in_dim[0]])

            # INPUT CONSTRAINTS DEFINITION

            for epsilon in epsilons:

                """if epsilon == 0.1 and ((heuristic == 'complete') and
                                       ('256' in shared_constants.id_arch_dict[net_id] or
                                        '128' in shared_constants.id_arch_dict[net_id])):
                    continue"""

                in_pred_mat = []
                in_pred_bias = []
                data_size = len(random_input)
                for i in range(len(random_input)):

                    lb_constraint = np.zeros(data_size)
                    ub_constraint = np.zeros(data_size)
                    lb_constraint[i] = -1
                    ub_constraint[i] = 1
                    in_pred_mat.append(lb_constraint)
                    in_pred_mat.append(ub_constraint)
                    # Errata Corrige: we wrongly used -1 and 1 as upper and lower bounds for all the variables. Now it is correct.
                    if random_input[i] - epsilon < in_low_b[i]:
                        in_pred_bias.append([-in_low_b[i]])
                    else:
                        in_pred_bias.append([-(random_input[i] - epsilon)])

                    if random_input[i] + epsilon > in_high_b[i]:
                        in_pred_bias.append([in_high_b[i]])
                    else:
                        in_pred_bias.append([random_input[i] + epsilon])

                in_pred_bias = np.array(in_pred_bias)
                in_pred_mat = np.array(in_pred_mat)

                in_prop = ver.NeVerProperty(in_pred_mat, in_pred_bias, [], [])

                verifier = ver.NeverVerification(heuristic="best_n_neurons", params=ver_param)

                start = time.perf_counter()
                output_starset, computing_time = verifier.get_output_starset(net, in_prop)
                lbs = []
                ubs = []
                for star in output_starset.stars:
                    lb, ub = star.get_bounds(0)
                    lbs.append(lb)
                    ubs.append(ub)

                min_lb = np.min(np.array(lbs))
                max_ub = np.max(np.array(ubs))

                delta = max(numpy_output[0] - min_lb, max_ub - numpy_output[0])
                stop = time.perf_counter()

                # Add output data to property and print
                in_prop.out_coef_mat = [np.array([[-1, 1]]).T]
                in_prop.out_bias_mat = [np.array([[numpy_output[0] + delta,
                                                   delta - numpy_output[0]]]).T]
                in_prop.to_smt_file('X', 'Y', f"prop_vnnlib_{net_id}_eps_{str(epsilon).replace('.', '')}.vnnlib")

                logger_exp_file.info(f"{net_id}, {heuristic}, {epsilon}, {min_lb}, {max_ub}, {delta}, {stop - start}")
                logger_exp_stream.info(f"{net_id}, {heuristic}, {epsilon}, {min_lb}, {max_ub}, {delta}, {stop - start}")
