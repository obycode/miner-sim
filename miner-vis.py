#!/usr/bin/env python3

import sqlite3
import sys
from graphviz import Digraph
import datetime
import os


tracked_miners = os.getenv("TRACKED_MINERS", "").split(",")
named_miners_raw = os.getenv("NAMED_MINERS", "").split(",")
if len(tracked_miners) == len(named_miners_raw):
    named_miners = dict(zip(tracked_miners, named_miners_raw))
else:
    print(
        "Error: The lists tracked_miners and named_miners_raw have different lengths."
    )
    named_miners = {}


class Commit:
    def __init__(
        self,
        block_header_hash,
        sender,
        burn_block_height,
        spend,
        sortition_id,
        parent=None,
        canonical=False,
    ):
        self.block_header_hash = block_header_hash
        self.sender = sender[1:-1]  # Remove quotes
        self.burn_block_height = burn_block_height
        self.spend = spend
        self.sortition_id = sortition_id
        self.parent = parent
        self.tracked = self.sender in tracked_miners
        self.children = False  # Initially no children
        self.canonical = canonical

    def __repr__(self):
        return f"Commit({self.block_header_hash[:8]}, Burn Block Height: {self.burn_block_height}, Spend: {self.spend:,}, Children: {self.children})"


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


def mark_canonical_blocks(db_file, commits):
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    tip = cursor.execute(
        "SELECT canonical_stacks_tip_hash FROM snapshots ORDER BY block_height DESC LIMIT 1;"
    ).fetchone()[0]

    while tip:
        commits[tip].canonical = True
        tip = commits[tip].parent


def create_graph(commits, sortition_sats):
    dot = Digraph(comment="Mining Status")

    # Keep track of a representative node for each cluster to enforce order
    last_height = None

    # Group nodes by block_height and create edges to parent nodes
    for block_height in sorted(
        set(commit.burn_block_height for commit in commits.values())
    ):
        tracked_spend = 0
        with dot.subgraph(name=f"cluster_{block_height}") as c:
            for commit in filter(
                lambda x: x.burn_block_height == block_height, commits.values()
            ):
                sender = named_miners.get(commit.sender)
                if not sender:
                    sender = commit.sender[0:8]
                node_label = f"{sender}\n{round(commit.spend/1000.0):,}K ({commit.spend/sortition_sats[commit.sortition_id]:.0%})"

                if commit.tracked:
                    tracked_spend += commit.spend

                c.attr(
                    label=f"Burn Block Height: {commit.burn_block_height}\nTotal Spend: {sortition_sats[commit.sortition_id]:,}\nTracked Spend: {tracked_spend:,} ({tracked_spend/sortition_sats[commit.sortition_id]:.2%})"
                )

                # Apply different styles if the node has children
                fillcolor = "white"
                color = "black"
                penwidth = "1"
                style = ""
                if commit.children:
                    color = "blue"
                    penwidth = "4"
                if commit.sender in tracked_miners:
                    fillcolor = "aquamarine"
                    style = "filled"
                if not commit.canonical:
                    style = f"{style},dashed"
                    penwidth = "1"
                else:
                    style = f"{style},solid"
                    penwidth = "4"
                c.node(
                    commit.block_header_hash,
                    node_label,
                    color=color,
                    fillcolor=fillcolor,
                    penwidth=penwidth,
                    style=style,
                )
                if commit.parent:
                    # If the parent is not the previous block, color it red
                    color = "black"
                    penwidth = "1"
                    if commits[commit.parent].burn_block_height != last_height:
                        color = "red"
                        penwidth = "4"
                    c.edge(
                        commit.parent,
                        commit.block_header_hash,
                        color=color,
                        penwidth=penwidth,
                    )

            last_height = block_height

    dot.render("output/mining_status.gv", format="png")


def collect_stats(commits):
    tracked_commits_per_block = {}
    wins = 0
    for commit in commits.values():
        if commit.sender in tracked_miners:
            # Keep an array of all tracked commits per block
            tracked_commits_per_block[
                commit.burn_block_height
            ] = tracked_commits_per_block.get(commit.burn_block_height, [])
            tracked_commits_per_block[commit.burn_block_height].append(commit.spend)

            # Count the number of wins
            if commit.children:
                wins += 1

    if len(tracked_commits_per_block) == 0:
        print("No tracked commits found")
        return {
            "avg_spend_per_block": 0,
            "win_percentage": 0,
        }

    # Print stats
    spend = 0
    for spends in tracked_commits_per_block.values():
        spend += sum(spends)

    return {
        "avg_spend_per_block": round(spend / len(tracked_commits_per_block)),
        "win_percentage": wins / len(tracked_commits_per_block),
    }


def generate_html(n_blocks, image_path, stats):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Block Commits Visualization</title>
        <style>
            .responsive-img {{
                width: 100%;
                height: auto;
            }}
            table, th, td {{
                border: 1px solid black;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 5px;
                text-align: left;
            }}
        </style>
    </head>
    <body>
        <p>This page was last updated at: {current_time}<br>Note: Data refreshes every minute. Refresh the page for the latest.</p>
        <h1>Last {n_blocks} Blocks</h1>
        <h2>Statistics</h2>
        <table>
            <tr><th>Average Spend per Block</th><td>{stats['avg_spend_per_block']:,}</td></tr>
            <tr><th>Win Percentage</th><td>{stats['win_percentage']:.2%}</td></tr>
        </table>
        <h2>Block Commits</h2>
        <a href="{image_path}" target="_blank">
            <img src="{image_path}" alt="Block Commits Graph" class="responsive-img">
        </a>
    </body>
    </html>
    """
    return html_content


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python script.py <path_to_database> <last_n_blocks>")
        sys.exit(1)

    db_path = sys.argv[1]
    last_n_blocks = int(sys.argv[2])
    commits, sortition_sats = get_block_commits_with_parents(db_path, last_n_blocks)
    mark_canonical_blocks(db_path, commits)

    create_graph(commits, sortition_sats)

    stats = collect_stats(commits)
    print(f"Avg spend per block: {stats['avg_spend_per_block']:,} Sats")
    print(f"Win %: {stats['win_percentage']:.2%}")

    # Generate and save HTML content
    html_content = generate_html(last_n_blocks, "mining_status.gv.png", stats)
    with open("output/index.html", "w") as file:
        file.write(html_content)
