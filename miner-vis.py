import sqlite3
import sys
from graphviz import Digraph

def get_block_commits_with_parents(db_file, last_n_blocks=1000):
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Fetch the necessary data to build the graph
    query = """
    SELECT
        block_header_hash,
        sortition_id,
        block_height,
        burn_fee,
        parent_block_ptr,
        parent_vtxindex
    FROM
        block_commits
    WHERE
        block_height > (SELECT MAX(block_height) FROM block_commits) - ?
    ORDER BY
        block_height ASC
    """
    cursor.execute(query, (last_n_blocks,))
    commits = cursor.fetchall()

    # Fetch parent block_header_hash using parent_block_ptr and parent_vtxindex
    parents_query = """
    SELECT
        block_header_hash
    FROM
        block_commits
    WHERE
        block_height = ? AND vtxindex = ?
    """
    # Prepare a dictionary to hold the parent hashes
    parent_hashes = {}
    for _, _, _, _, parent_block_ptr, parent_vtxindex in commits:
        if (parent_block_ptr, parent_vtxindex) not in parent_hashes:
            cursor.execute(parents_query, (parent_block_ptr, parent_vtxindex))
            result = cursor.fetchone()
            parent_hashes[(parent_block_ptr, parent_vtxindex)] = result[0] if result else None

    conn.close()
    return commits, parent_hashes

def create_graph(commits, parent_hashes):
    dot = Digraph(comment='Mining Status')

    # Group nodes by sortition_id and create edges to parent nodes
    for block_header_hash, sortition_id, block_height, burn_fee, parent_block_ptr, parent_vtxindex in commits:
        print(f'Block Header Hash: {block_header_hash}')
        with dot.subgraph(name=f'cluster_{sortition_id}') as c:
            c.attr(label=f'Sortition ID: {sortition_id}\nBlock Height: {block_height}')
            c.node(block_header_hash, f'{block_header_hash}\Sats Spent: {burn_fee}')
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
    commits, parent_hashes = get_block_commits_with_parents(db_path, last_n_blocks)

    create_graph(commits, parent_hashes)
