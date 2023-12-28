#!/usr/bin/env python3

import sqlite3
import sys
from graphviz import Digraph


class Commit:
    def __init__(
        self,
        block_header_hash,
        sender,
        burn_block_height,
        spend,
        sortition_id,
        parent=None,
    ):
        self.block_header_hash = block_header_hash
        self.sender = sender
        self.burn_block_height = burn_block_height
        self.spend = spend
        self.sortition_id = sortition_id
        self.parent = parent
        self.children = False  # Initially no children

    def __repr__(self):
        return f"Commit({self.block_header_hash[:8]}, Burn Block Height: {self.burn_block_height}, Spend: {self.spend}, Children: {self.children})"


def get_block_commits_with_parents(db_file, last_n_blocks=1000):
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Pre-compute the maximum block height
    cursor.execute("SELECT MAX(block_height) FROM block_commits")
    max_block_height = cursor.fetchone()[0]
    lower_bound_height = max_block_height - last_n_blocks

    # Fetch the necessary data to build the graph
    query = """
    SELECT
        block_header_hash,
        apparent_sender,
        sortition_id,
        vtxindex,
        block_height,
        burn_fee,
        parent_block_ptr,
        parent_vtxindex
    FROM
        block_commits
    WHERE
        block_height > ?
    ORDER BY
        block_height ASC
    """
    cursor.execute(query, (lower_bound_height,))
    raw_commits = cursor.fetchall()

    # Prepare dictionaries to hold the parent hashes and total spends
    parent_hashes = {}
    sortition_sats = {}
    commits = {}  # Track all nodes, by block_header_hash

    for (
        block_header_hash,
        apparent_sender,
        sortition_id,
        vtxindex,
        block_height,
        burn_fee,
        parent_block_ptr,
        parent_vtxindex,
    ) in raw_commits:
        parent = parent_hashes.get((parent_block_ptr, parent_vtxindex))
        if parent:
            commits[parent].children = True

        commits[block_header_hash] = Commit(
            block_header_hash,
            apparent_sender,
            block_height,
            int(burn_fee),
            sortition_id,
            parent,
        )
        parent_hashes[(block_height, vtxindex)] = block_header_hash
        sortition_sats[sortition_id] = sortition_sats.get(sortition_id, 0) + int(
            burn_fee
        )

    conn.close()
    return commits, sortition_sats


def create_graph(commits, sortition_sats):
    dot = Digraph(comment="Mining Status")
    forks = 0

    # Group nodes by sortition_id and create edges to parent nodes
    for commit in commits.values():
        truncated_sender = commit.sender[1:9]
        node_label = f"{truncated_sender}\nSpend: {commit.spend} ({commit.spend/sortition_sats[commit.sortition_id]:.2%})"
        with dot.subgraph(name=f"cluster_{commit.sortition_id}") as c:
            c.attr(
                label=f"Block Height: {commit.burn_block_height}\nTotal Spend: {sortition_sats[commit.sortition_id]}"
            )
            # Apply different styles if the node has children
            if commit.children:
                c.node(
                    commit.block_header_hash,
                    node_label,
                    style="filled",
                    color="darkslategray2",
                    penwidth="2",
                )
            else:
                c.node(commit.block_header_hash, node_label)
            if commit.parent:
                # If the parent is not the previous block, color it red
                color = "black"
                if (
                    commits[commit.parent].burn_block_height
                    < commit.burn_block_height - 1
                ):
                    forks += 1
                    color = "red"
                c.edge(commit.parent, commit.block_header_hash, color=color)

    # Add global graph label (can be used as a footer or header)
    graph_metadata = f"Summary Info:\n- Forks: {forks}"
    dot.attr(label=graph_metadata, labelloc="t", fontsize="10")

    dot.render("output/mining_status.gv", view=True, format="png")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python script.py <path_to_database> <last_n_blocks>")
        sys.exit(1)

    db_path = sys.argv[1]
    last_n_blocks = int(sys.argv[2])
    commits, sortition_sats = get_block_commits_with_parents(db_path, last_n_blocks)
    create_graph(commits, sortition_sats)
