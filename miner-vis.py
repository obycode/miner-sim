import sqlite3
import sys
from graphviz import Digraph

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
    commits = cursor.fetchall()

    # Prepare a dictionary to hold the parent hashes
    parent_hashes = {}
    sortition_sats = {}
    for block_header_hash, sortition_id, vtxindex, block_height, burn_fee, _, _ in commits:
        parent_hashes[(block_height, vtxindex)] = block_header_hash
        sortition_sats[sortition_id] = sortition_sats.get(sortition_id, 0) + int(burn_fee)

    conn.close()
    return commits, parent_hashes, sortition_sats

def create_graph(commits, parent_hashes, sortition_sats):
    dot = Digraph(comment='Mining Status')

    # Group nodes by sortition_id and create edges to parent nodes
    for block_header_hash, sortition_id, _, block_height, burn_fee, parent_block_ptr, parent_vtxindex in commits:
        print(f'Block Header Hash: {block_header_hash}')
        with dot.subgraph(name=f'cluster_{sortition_id}') as c:
            sortition_spend = sortition_sats.get(sortition_id, 0)
            c.attr(label=f'Block Height: {block_height}\nTotal Spend: {sortition_spend}')
            c.node(block_header_hash, f'{block_header_hash[:8]}\nSpend: {int(burn_fee)/sortition_spend:.2%}')
            parent_hash = parent_hashes.get((parent_block_ptr, parent_vtxindex))
            if parent_hash:
                c.edge(parent_hash, block_header_hash)

    dot.render('output/mining_status.gv', view=True, format='png')

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python script.py <path_to_database> <last_n_blocks>")
        sys.exit(1)

    db_path = sys.argv[1]
    last_n_blocks = int(sys.argv[2])
    commits, parent_hashes, sortition_sats = get_block_commits_with_parents(db_path, last_n_blocks)

    create_graph(commits, parent_hashes, sortition_sats)
