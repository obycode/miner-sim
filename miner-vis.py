#!/usr/bin/env python3

import argparse
import json
import requests
import os
import sqlite3
import sys
from graphviz import Digraph
import datetime
import re
import toml
from flask import Flask, request, abort, send_from_directory

sortition_db = "mainnet/burnchain/sortition/marf.sqlite"
chainstate_db = "mainnet/chainstate/vm/index.sqlite"
default_color = "white"
cost_limits = {
    "write_length": 15_000_000,
    "write_count": 15_000,
    "read_length": 100_000_000,
    "read_count": 15_000,
    "runtime": 5_000_000_000,
    "size": 2 * 1024 * 1024,
}


def is_miner_known(miner_config, identifier):
    # Check if the miner is in the config
    return identifier in miner_config.get("miners", {})


def is_miner_tracked(miner_config, identifier):
    # Check if the miner is in the config and is tracked
    miner_info = miner_config.get("miners", {}).get(identifier)
    if miner_info and miner_info.get("track", False):
        return True
    return False


def get_miner_color(miner_config, identifier):
    # Retrieve the color for the given miner identifier
    miner_info = miner_config.get("miners", {}).get(identifier)
    if miner_info:
        return miner_info.get("color", default_color)
    return default_color


def get_miner_name(miner_config, identifier):
    # Retrieve the name for the given miner identifier
    miner_info = miner_config.get("miners", {}).get(identifier)
    if miner_info:
        return miner_info.get("name", identifier[0:8])
    return identifier[0:8]


class Commit:
    def __init__(
        self,
        block_header_hash,
        txid,
        sender,
        burn_block_height,
        spend,
        sortition_id,
        parent=None,
        stacks_height=None,
        block_hash=None,
        won=False,
        canonical=False,
        tip=False,
        coinbase_earned=0,
        fees_earned=0,
        read_length=0,
        read_count=0,
        write_length=0,
        write_count=0,
        runtime=0,
        block_size=0,
    ):
        self.block_header_hash = block_header_hash
        self.txid = txid
        self.sender = sender[1:-1]  # Remove quotes
        self.burn_block_height = burn_block_height
        self.spend = spend
        self.sortition_id = sortition_id
        self.parent = parent
        self.stacks_height = stacks_height
        self.block_hash = block_hash
        self.won = won
        self.canonical = canonical
        self.tip = tip
        self.coinbase_earned = coinbase_earned
        self.fees_earned = fees_earned
        self.read_length = read_length
        self.read_count = read_count
        self.write_length = write_length
        self.write_count = write_count
        self.runtime = runtime
        self.block_size = block_size

    def __repr__(self):
        return f"Commit({self.block_header_hash[:8]}, Burn Block Height: {self.burn_block_height}, Spend: {self.spend:,}, Children: {self.children})"

    def get_fullness(self):
        fullness = max(
            self.read_length / cost_limits["read_length"],
            self.read_count / cost_limits["read_count"],
            self.write_length / cost_limits["write_length"],
            self.write_count / cost_limits["write_count"],
            self.runtime / cost_limits["runtime"],
            self.block_size / cost_limits["size"],
        )
        return round(fullness * 100, 2)


class Miner:
    def __init__(self, address, name, color, tracked, known):
        self.address = address
        self.name = name
        self.color = color
        self.tracked = tracked
        self.known = known

    def __repr__(self):
        return f"Miner({self.identifier}, {self.name}, {self.color}, {self.track})"


def get_block_commits_with_parents(db_path, last_n_blocks=1000):
    conn = sqlite3.connect(os.path.join(db_path, sortition_db))
    cursor = conn.cursor()

    # Pre-compute the maximum block height
    cursor.execute("SELECT MAX(block_height) FROM block_commits")
    max_block_height = cursor.fetchone()[0]
    lower_bound_height = max_block_height - last_n_blocks

    # Fetch the necessary data to build the graph
    query = """
    SELECT
        block_header_hash,
        txid,
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
        txid,
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
            txid,
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


def mark_canonical_blocks(db_path, commits):
    conn = sqlite3.connect(os.path.join(db_path, sortition_db))
    cursor = conn.cursor()

    for block_height in sorted(
        set(commit.burn_block_height for commit in commits.values())
    ):
        (winning_block_txid, stacks_height, consensus_hash) = cursor.execute(
            "SELECT winning_block_txid, stacks_block_height, consensus_hash FROM snapshots WHERE block_height = ?;",
            (block_height,),
        ).fetchone()

        for commit in filter(
            lambda x: x.burn_block_height == block_height, commits.values()
        ):
            if winning_block_txid == commit.txid:
                commit.won = True
                commit.stacks_height = stacks_height

                # Fetch the coinbase and fees earned
                chainstate_conn = sqlite3.connect(os.path.join(db_path, chainstate_db))
                chainstate_cursor = chainstate_conn.cursor()
                # Execute the query
                result = chainstate_cursor.execute(
                    "SELECT block_hash, coinbase, tx_fees_anchored, tx_fees_streamed FROM payments WHERE consensus_hash = ?;",
                    (consensus_hash,),
                ).fetchone()

                if result:
                    (
                        block_hash,
                        coinbase,
                        tx_fees_anchored,
                        tx_fees_streamed,
                    ) = result
                    commit.block_hash = block_hash
                    commit.coinbase_earned = int(coinbase)
                    commit.fees_earned = int(tx_fees_anchored) + int(tx_fees_streamed)

                # Fetch the block costs and size
                result = chainstate_cursor.execute(
                    "SELECT cost, block_size FROM block_headers WHERE block_hash = ?;",
                    (block_hash,),
                ).fetchone()
                if result:
                    cost_string, block_size = result
                    costs = json.loads(cost_string)
                    commit.read_length = int(costs["read_length"])
                    commit.read_count = int(costs["read_count"])
                    commit.write_length = int(costs["write_length"])
                    commit.write_count = int(costs["write_count"])
                    commit.runtime = int(costs["runtime"])
                    commit.block_size = int(block_size)

                chainstate_conn.close()

            else:
                if commit.parent and commit.parent in commits:
                    parent_commit = commits[commit.parent]
                    commit.stacks_height = parent_commit.stacks_height + 1

    # Mark the canonical chain
    tip = cursor.execute(
        "SELECT canonical_stacks_tip_hash FROM snapshots ORDER BY block_height DESC LIMIT 1;"
    ).fetchone()[0]

    commits[tip].tip = True
    while tip:
        commits[tip].canonical = True
        tip = commits[tip].parent


def create_graph(miner_config, commits, sortition_sats):
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
                node_label = f"""{get_miner_name(miner_config, commit.sender)}
{round(commit.spend/1000.0):,}K ({commit.spend/sortition_sats[commit.sortition_id]:.0%})
Height: {commit.stacks_height}"""
                if commit.won:
                    node_label += f"\n{commit.get_fullness()}% full"

                if miner_config.get("track_all", False) or is_miner_tracked(
                    miner_config, commit.sender
                ):
                    tracked_spend += commit.spend

                c.attr(
                    label=f"""Burn Block Height: {commit.burn_block_height}
Total Spend: {sortition_sats[commit.sortition_id]:,}
Tracked Spend: {tracked_spend:,} ({tracked_spend/sortition_sats[commit.sortition_id]:.2%})"""
                )

                # Initialize the node attributes dictionary
                node_attrs = {
                    "color": "blue" if commit.won else "black",
                    "fillcolor": get_miner_color(miner_config, commit.sender),
                    "penwidth": "8" if commit.tip else "4" if commit.won else "1",
                    "style": "filled,solid",
                }

                # Additional modifications based on conditions
                if not commit.canonical:
                    node_attrs["style"] = "filled,dashed"

                # Check if the commit spent more than the alert_sats threshold
                if commit.spend > miner_config.get("alert_sats", 1000000):
                    node_attrs["fontcolor"] = "red"
                    node_attrs["fontname"] = "bold"

                # If we have the block hash, link to the explorer
                if commit.block_hash:
                    node_attrs[
                        "href"
                    ] = f"https://explorer.hiro.so/block/0x{commit.block_hash}"

                # Now use the dictionary to set attributes
                c.node(commit.block_header_hash, node_label, **node_attrs)

                if commit.parent:
                    # If the parent is not the previous block, color the edge red
                    color = "black"
                    penwidth = "1"
                    if commits[commit.parent].burn_block_height != last_height:
                        color = "red"
                        penwidth = "4"
                    if commit.canonical:
                        color = "blue"
                        penwidth = "8"
                    c.edge(
                        commit.parent,
                        commit.block_header_hash,
                        color=color,
                        penwidth=penwidth,
                    )

            last_height = block_height

    return dot.pipe(format="svg").decode("utf-8")


def collect_stats(miner_config, commits):
    tracked_commits_per_block = {}
    miners = {}
    wins = 0
    canonical = 0
    coinbase_earned = 0
    fees_earned = 0
    orphans = 0
    total_blocks = 0
    for commit in commits.values():
        # Track the overall orphan rate
        if commit.won:
            total_blocks += 1
            if not commit.canonical:
                orphans += 1

        if miner_config.get("track_all", False) or is_miner_tracked(
            miner_config, commit.sender
        ):
            # Keep an array of all tracked commits per block
            tracked_commits_per_block[
                commit.burn_block_height
            ] = tracked_commits_per_block.get(commit.burn_block_height, [])
            tracked_commits_per_block[commit.burn_block_height].append(commit.spend)

            # Count the number of wins
            if commit.won:
                wins += 1

            # Count the number of canonical blocks
            if commit.canonical:
                canonical += 1

                # Sum the coinbase and fees earned
                coinbase_earned += commit.coinbase_earned
                fees_earned += commit.fees_earned

        if commit.sender not in miners:
            miners[commit.sender] = Miner(
                commit.sender,
                get_miner_name(miner_config, commit.sender),
                get_miner_color(miner_config, commit.sender),
                is_miner_tracked(miner_config, commit.sender),
                is_miner_known(miner_config, commit.sender),
            )

    if len(tracked_commits_per_block) == 0:
        print("No tracked commits found")
        return {
            "total_spend": 0,
            "total_coinbase_earned": 0,
            "total_fees_earned": 0,
            "avg_spend_per_block": 0,
            "win_percentage": 0,
            "canonical_percentage": 0,
            "miners": miners,
            "orphan_rate": orphans / total_blocks if total_blocks > 0 else 0,
        }

    # Print stats
    spend = 0
    for spends in tracked_commits_per_block.values():
        spend += sum(spends)

    return {
        "total_spend": spend,
        "total_coinbase_earned": coinbase_earned,
        "total_fees_earned": fees_earned,
        "avg_spend_per_block": round(spend / len(tracked_commits_per_block)),
        "win_percentage": wins / len(tracked_commits_per_block),
        "canonical_percentage": canonical / len(tracked_commits_per_block),
        "miners": miners,
        "orphan_rate": orphans / total_blocks if total_blocks > 0 else 0,
    }


def generate_html(n_blocks, svg_content, stats):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Use regex to replace width and height attributes in the SVG
    svg_content = re.sub(r'width="\d+pt"', 'width="100%"', svg_content)
    svg_content = re.sub(r'height="\d+pt"', 'height="100%"', svg_content)

    # Compute the price ratio (if applicable)
    if (stats["total_coinbase_earned"] + stats["total_fees_earned"]) != 0:
        price_ratio = stats["total_spend"] / (
            (stats["total_coinbase_earned"] + stats["total_fees_earned"]) / 1000000.0
        )
        price_ratio_str = f"{price_ratio:.2f} Sats/STX"
    else:
        # Handle the division by zero case
        price_ratio_str = "N/A"

    miner_rows = "".join(
        f"""
            <tr>
                <td>{miner.name}</td>
                <td>{miner.address}</td>
                <td><span class="color-sample" style="background-color: {miner.color};"></span>{miner.color}</td>
                <td class="center-text">{"✅" if miner.tracked else ""}</td>
            </tr>
            """
        for miner in stats["miners"].values()
    )

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Block Commits Visualization</title>
        <style>
            .responsive-svg {{
                max-width: 100%;
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
            .color-sample {{
                width: 20px;
                height: 20px;
                border-radius: 50%;
                border: 2px solid black;
                display: inline-block;
                margin-right: 5px;
            }}
            .center-text {{
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <nav>
            <ul style="list-style-type: none; padding: 0;">
                <li style="display: inline; margin-right: 20px;"><a href="/">Home</a></li>
                <li style="display: inline; margin-right: 20px;"><a href="/50.html">50 Blocks</a></li>
                <li style="display: inline;"><a href="/100.html">100 Blocks</a></li>
            </ul>
        </nav>
        <p>This page was last updated at: {current_time}<br>Note: Data refreshes every minute. Refresh the page for the latest.</p>
        <h1>Last {n_blocks} Blocks</h1>
        <h2>Statistics</h2>
        <table>
            <tr><th>Total Spend</th><td>{stats['total_spend']:,}</td></tr>
            <tr><th>Total Coinbase Earned</th><td>{(stats['total_coinbase_earned']/1000000.0):,} STX</td></tr>
            <tr><th>Total Fees Earned</th><td>{(stats['total_fees_earned']/1000000.0):,} STX</td></tr>
            <tr><th>Total Earned</th><td>{((stats['total_coinbase_earned'] + stats['total_fees_earned'])/1000000.0):,} STX</td></tr>
            <tr><th>Average Spend per Block</th><td>{stats['avg_spend_per_block']:,}</td></tr>
            <tr><th>Win Percentage</th><td>{stats['win_percentage']:.2%}</td></tr>
            <tr><th>Canonical Percentage</th><td>{stats['canonical_percentage']:.2%}</td></tr>
            <tr><th>Price Ratio</th><td>{price_ratio_str} Sats/STX</td></tr>
            <tr><th>Network Orphan Rate</th><td>{stats['orphan_rate']:.2%}</td></tr>
        </table>
        <h2>Block Commits</h2>
        <div class="responsive-svg">
            {svg_content}
        </div>
        <h2>Legend</h2>
        <table>
            <tr>
                <th>Name</th>
                <th>Address</th>
                <th>Color</th>
                <th>Tracked?</th>
            </tr>
            {miner_rows}
        </table>
    </body>
    </html>
    """
    return html_content


def send_new_miner_alerts(miner_config, stats):
    webhook = miner_config.get("alert_webhook")
    if not webhook:
        return

    # Send an alert if there are any new miners
    new_miners = list(filter(lambda x: not x.known, stats["miners"].values()))
    if len(new_miners) == 0:
        return

    data_to_send = {"new_miners": list(map(lambda x: x.address, new_miners))}
    json_data = json.dumps(data_to_send)

    response = requests.post(
        webhook, data=json_data, headers={"Content-Type": "application/json"}
    )

    # Check if the POST request was successful
    if response.status_code != 200:
        print(
            f"Failed to send miner alert. Status code: {response.status_code}, Response: {response.text}"
        )

    # Update the config file with the new miners, so that we don't send alerts again
    for miner in new_miners:
        miner_config["miners"][miner.address] = {
            "name": miner.name,
            "color": miner.color,
            "track": False,
        }
    with open(args.config_path, "w") as file:
        toml.dump(miner_config, file)


def run_server(args):
    app = Flask(__name__, static_folder="output", static_url_path="")

    @app.route("/new_block", methods=["POST"])
    def new_block():
        # Check if the request is from localhost
        if request.remote_addr != "127.0.0.1":
            abort(403)  # Forbidden access

        print("Received new block notification")
        run_command_line(args)
        return "Graphs rebuilt", 200

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    @app.route("/<path:path>")
    def static_file(path):
        return send_from_directory(app.static_folder, path)

    app.run(host="0.0.0.0", port=8080)


def run_command_line(args):
    with open(args.config_path, "r") as file:
        miner_config = toml.load(file)

    for index, last_n_blocks in enumerate(args.block_counts):
        print(f"Generating graph for last {last_n_blocks} blocks...")
        commits, sortition_sats = get_block_commits_with_parents(
            miner_config.get("db_path"), last_n_blocks
        )
        mark_canonical_blocks(miner_config.get("db_path"), commits)

        svg_string = create_graph(miner_config, commits, sortition_sats)

        stats = collect_stats(miner_config, commits)

        send_new_miner_alerts(miner_config, stats)

        print(f"Total spend: {stats['total_spend']:,} Sats")
        print(
            f"Total coinbase earned: {(stats['total_coinbase_earned']/1000000.0):,} STX"
        )
        print(f"Total fees earned: {(stats['total_fees_earned']/1000000.0):,} STX")
        print(
            f"Total earned: {((stats['total_coinbase_earned'] + stats['total_fees_earned'])/1000000.0):,} STX"
        )
        print(f"Avg spend per block: {stats['avg_spend_per_block']:,} Sats")
        print(f"Win %: {stats['win_percentage']:.2%}")
        print(f"Canonical %: {stats['canonical_percentage']:.2%}")
        if stats["total_coinbase_earned"] + stats["total_fees_earned"] > 0:
            print(
                f"Price ratio: {stats['total_spend']/((stats['total_coinbase_earned'] + stats['total_fees_earned'])/1000000.0):.2f} Sats/STX"
            )
        else:
            print("Price ratio: N/A")
        print(f"Network orphan rate: {stats['orphan_rate']:.2%}")

        # Generate and save HTML content
        html_content = generate_html(last_n_blocks, svg_string, stats)

        basename = "index" if index == 0 else f"{last_n_blocks}"
        with open(f"output/{basename}.html", "w") as file:
            file.write(html_content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the script in observer or command-line mode."
    )
    parser.add_argument(
        "--observer", action="store_true", help="Run in observer mode (as a server)"
    )
    parser.add_argument(
        "config_path", nargs="?", help="Path to the miner configuration file"
    )
    parser.add_argument(
        "block_counts",
        nargs="*",
        type=int,
        default=[20, 50, 100],
        help="Number of blocks to analyze (optional, defaults to [20, 50, 100])",
    )

    args = parser.parse_args()

    if not args.config_path:
        parser.print_help()
        sys.exit(1)

    if args.observer:
        print("Running in observer mode...")
        run_server(args)
    else:
        run_command_line(args)
