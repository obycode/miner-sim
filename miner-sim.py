#!/usr/bin/env python3

import random
from graphviz import Digraph
import argparse


class HonestMiner:
    def __init__(self, miner_id):
        self.miner_id = f"H{miner_id}"
        self.blocks_mined = 0

    def mine_block(self, blockchain):
        parent = blockchain.get_longest_chain()
        blockchain.add_block(self, parent)
        self.blocks_mined += 1


class ColludingMiner:
    def __init__(self, miner_id, gap):
        self.miner_id = f"C{miner_id}"
        self.gap = gap
        self.blocks_mined = 0

    def mine_block(self, blockchain):
        parent = blockchain.get_longest_colluding_chain(self.gap)
        blockchain.add_block(self, parent)
        self.blocks_mined += 1


class Block:
    def __init__(self, id, miner_id, parent, height):
        self.id = id
        self.miner_id = miner_id
        self.parent = parent
        self.height = height


class Fork:
    def __init__(self, base, tip):
        self.base = base
        self.tip = tip

    def depth(self):
        return self.tip.height - self.base.height

    def __gt__(self, other):
        self.depth() > other.depth()


class Blockchain:
    def __init__(self):
        self.blocks = [Block(0, "genesis", None, 0)]
        self.tip = self.blocks[0]
        self.colluding_tip = self.blocks[0]
        self.forks = {}
        self.dot = Digraph()
        self.dot.node("0", "genesis")

    def add_block(self, miner, parent):
        block_id = len(self.blocks)
        height = parent.height + 1
        block = Block(block_id, miner.miner_id, parent.id, height)
        self.blocks.append(block)

        # Track forks
        if parent != self.tip and height <= self.tip.height:
            to_update = self.forks.pop(parent.id, None)
            if to_update != None:
                self.forks[block_id] = Fork(to_update.base, block)
            else:
                self.forks[block_id] = Fork(parent, block)

        if height > self.tip.height:
            self.tip = self.blocks[-1]
        if type(miner) == ColludingMiner and height > self.colluding_tip.height:
            self.colluding_tip = self.blocks[-1]

        node_attrs = {}
        if type(miner) == ColludingMiner:
            node_attrs['color'] = "red"
            node_attrs['style'] = "filled"
            node_attrs['fillcolor'] = "lightpink"

        if block_id == self.tip.id:
            node_attrs['color'] = "blue"
            node_attrs['penwidth'] = "2"

        self.dot.node(str(block_id), label=f"{block_id} ({height})",
                      **node_attrs)
        self.dot.edge(str(parent.id), str(block_id), label=f"{miner.miner_id}")

    def get_longest_chain(self):
        return self.tip

    def get_longest_colluding_chain(self, gap):
        # If the colluding tip is more than `gap` back from the longest chain,
        # revert to mining on the longest chain.
        if self.tip.height > self.colluding_tip.height + gap:
            return self.tip
        return self.colluding_tip

    # Taking the current longest chain, print the miner statistics.
    def print_statistics(self, miners, verbose=False):
        print("Fork statistics:")
        print("--------------------")
        print(f"  Num forks: {len(self.forks)}")
        max_depth = 0
        abandoned = 0
        for fork in self.forks.values():
            if verbose:
                print(
                    f"  * From height {fork.base.height+1} to {fork.tip.height}")
            abandoned += fork.depth()
            max_depth = max(max_depth, fork.depth())
        print(f"  Max depth: {max_depth}")
        print(
            f"  Abandoned blocks: {abandoned}/{len(self.blocks)-1} ({round(abandoned / (len(self.blocks) - 1) * 100, 2)}%)")
        print("--------------------")
        included_blocks = {miner.miner_id: 0 for miner in miners}
        block_id = self.tip.id
        while block_id is not None:
            block = self.blocks[block_id]
            if block.miner_id not in included_blocks:
                included_blocks[block.miner_id] = 0
            included_blocks[block.miner_id] += 1
            block_id = block.parent

        print("Miner statistics:")
        honest_mined = 0
        honest_confirmed = 0
        colluding_mined = 0
        colluding_confirmed = 0
        for miner in miners:
            score = 0 if miner.blocks_mined == 0 else round(
                included_blocks[miner.miner_id]/miner.blocks_mined * 100, 2)
            mined = "{:>4}".format(miner.blocks_mined)
            included = "{:>4}".format(included_blocks[miner.miner_id])
            if verbose:
                print(
                    f"  * {miner.miner_id}: {mined} blocks mined, {included} blocks included: {score}% confirmed")
            if type(miner) == HonestMiner:
                honest_mined += miner.blocks_mined
                honest_confirmed += included_blocks[miner.miner_id]
            else:
                colluding_mined += miner.blocks_mined
                colluding_confirmed += included_blocks[miner.miner_id]
        print("--------------------")
        honest_score = round(honest_confirmed / honest_mined * 100, 2) if honest_mined > 0 else 100
        colluding_score = round(colluding_confirmed / colluding_mined * 100, 2) if colluding_mined > 0 else 100
        print(f"  Honest miners:    {honest_score}% confirmed")
        print(f"  Colluding miners: {colluding_score}% confirmed")

    def save_graph(self, filename):
        self.dot.render(filename, view=True, format="png")


def simulate_mining(miners, num_rounds):
    blockchain = Blockchain()

    for _ in range(num_rounds):
        miner = random.choice(miners)
        miner.mine_block(blockchain)

    return blockchain


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simulate a mining scenario with honest and colluding miners.")
    parser.add_argument("--verbose", action='store_true',
                        help="Print more information")
    parser.add_argument("--honest", type=int, default=3,
                        help="Number of honest miners (default=3)")
    parser.add_argument("--colluding", type=int, default=2,
                        help="Number of colluding miners (default=2)")
    parser.add_argument("--rounds", type=int, default=10000,
                        help="Number of mining rounds to simulate (default=10000)")
    parser.add_argument("--gap", type=int, default=5,
                        help="Gap allowed on colluding fork (default=5)")
    parser.add_argument("--graph", action='store_true',
                        help="Generate a graph of the blockchain")

    args = parser.parse_args()

    # Generate a list of miner IDs
    miners = [HonestMiner(i) for i in range(1, args.honest + 1)] + \
        [ColludingMiner(i, args.gap) for i in range(1, args.colluding + 1)]

    blockchain = simulate_mining(miners, args.rounds)

    if args.graph:
        blockchain.save_graph("blockchain_simulation")
    blockchain.print_statistics(miners, args.verbose)
