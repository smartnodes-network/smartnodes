from src.p2p.connection import Connection
from src.p2p.smart_node import SmartDHTNode
from src.ml.model_analyzer import get_gpu_memory

import torch.nn as nn
import threading
import random
import hashlib
import pickle
import time


class TorchNode(SmartDHTNode):
    def __init__(
        self,
        host: str,
        port: int,
        wallet_address: str,
        debug: bool = False,
        max_connections: int = 0,
    ):
        super(TorchNode, self).__init__(
            host,
            port,
            wallet_address,
            debug=debug,
            max_connections=max_connections,
        )
        # State info
        self.available_memory = get_gpu_memory()
        self.state = 0

        # Model training parameters
        self.training = False
        self.master = False

        # Stores connections and their context
        self.nodes = {}
        self.node_stats = {}
        self.distributed_graph = {}
        self.updater_flag = threading.Event()

        self.key_hash = hashlib.sha256(self.rsa_pub_key).hexdigest()

    def stream_data(self, data: bytes, node: Connection) -> bool:
        try:
            if b"REQUESTS" == data[:8]:
                self.debug_print(f"RECEIVED STATS REQUEST")
                self.handle_statistics_request(node)
                return True

            elif b"RESPONSE" == data[:8]:
                self.debug_print(f"RECEIVED STATS")
                stats = pickle.loads(data[8:])
                node_hash = stats["id"]
                self.node_stats[node_hash] = stats

                return False

        except Exception as e:
            self.debug_print(f"torch_node->stream_data: {e}")
            raise e

    def send_tensor(self, tensor, node: Connection):
        # tensor_bytes = self.BoT + pickle.dumps(tensor) + self.EoT
        tensor_bytes = b"TENSOR" + pickle.dumps(tensor)
        self.debug_print(
            f"worker: sending {round(tensor_bytes.__sizeof__() / 1e9, 3)} GB"
        )
        self.send_to_node(node, tensor_bytes)

    def send_forward(self, node: Connection, args, context):
        pickled_data = b"FORWARD" + pickle.dumps((context, args))
        print(hashlib.sha256(pickled_data).hexdigest())
        self.send_to_node(node, pickled_data)

    def send_backward(self, node: Connection, args, context):
        pickled_data = b"BACKWARD" + pickle.dumps((context, args))
        self.send_to_node(node, pickled_data)

    def send_parameters(self, node: Connection, parameters, module_id):
        pickled_data = b"PARAMETERS" + pickle.dumps((module_id, list(parameters)))
        self.send_to_node(node, pickled_data)

    def send_parameters_req(self, node: Connection, module_id):
        self.send_to_node(node, b"PARAMSREQ" + module_id)

    def send_train_updated(self, node: Connection, mode: bool, module_id):
        tag = b"TU-REQ"
        mode = b"1" if mode else b"0"
        data = tag + mode + module_id
        self.send_to_node(node, data)

    def send_update_train_request(self, node: Connection, mode: bool, module_id):
        tag = b"UT-REQ"
        mode = b"1" if mode else b"0"
        data = tag + mode + module_id
        self.send_to_node(node, data)

    def send_module(self, module: nn.Module, node: Connection, prefix: bytes = b""):
        module.parent = self.key_hash
        module_bytes = pickle.dumps(module)
        module_bytes = prefix + b"MODULE" + module_bytes

        print("SENDING MODULE")
        self.send_to_node(node, module_bytes)
        time.sleep(1)

    def send_statistics_request(self, node):
        message = b"REQUESTS"
        self.send_to_node(node, message)

    def broadcast_statistics(self, callee, additional_context: dict = None):
        memory = self.available_memory

        stats = {
            "id": self.rsa_pub_key + self.port.to_bytes(4, "big"),
            "memory": memory,
            "state": self.state,
        }

        if additional_context is not None:
            for k, v in additional_context.items():
                if k not in stats.keys():
                    stats[k] = v

        stats_bytes = pickle.dumps((self.key_hash, stats))
        stats_bytes = b"RESPONSE" + stats_bytes
        self.send_to_node(callee, stats_bytes)

    # Iterate connected nodes and request their current state
    def update_worker_stats(self):
        while not self.updater_flag.is_set():

            for node in self.connections:
                # Beforehand, check the last time the worker has updated (self.prune_workers?)
                self.send_statistics_request(node)
                time.sleep(1)

            if self.nodes:
                self.updater_flag.set()

            time.sleep(5)

    # Iterate connected nodes and request their current state
    def request_worker_stats(self):
        while not self.updater_flag.is_set():

            for node in self.connections:
                # if hasattr(node, "")
                # Beforehand, check the last time the worker has updated (self.prune_workers?)
                self.send_statistics_request(node)
                time.sleep(1)

            # if self.nodes:
            #     self.updater_flag.set()

            time.sleep(5)

    def handle_statistics_request(self, callee, additional_context: dict = None):
        # memory = self.available_memory
        memory = 1e9

        stats = {
            "id": self.key_hash,
            "memory": memory,
            "role": self.state,
            "training": self.training,
            #         # "connection": self.connections[i], "latency_matrix": self.connections[i].latency
        }

        if additional_context is not None:
            for k, v in additional_context.items():
                if k not in stats.keys():
                    stats[k] = v

        stats_bytes = pickle.dumps(stats)
        stats_bytes = b"RESPONSE" + stats_bytes
        self.send_to_node(callee, stats_bytes)

    def select_candidate_worker(self):
        candidate_node = max(self.nodes.values(), key=lambda x: x["memory"])
        return candidate_node

    def bootstrap(self):
        num_validators = self.contract.functions.getValidatorIdCount().call()
        sample_size = min(num_validators, 10)  # Adjust sample size as needed

        # Randomly select sample_size validators
        random_sample = random.sample(range(1, num_validators + 1), sample_size)

        for validatorId in random_sample:
            # Get validator information from smart contract
            _, address, id_hash, reputation, active = (
                self.contract.functions.validators(validatorId).call()
            )

            host, port = self.query_routing_table(id_hash)

            # Connect to the validator's node and exchange information
            connected = self.connect_dht_node(host, port)

            # Check to see if connected, if not we can try another random node
            # if connected:
