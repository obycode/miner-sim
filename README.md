## Mining Simulator

This tool provides a way to simulate miners colluding to analyze how the
collusion affects the network and profitability of honest miners.

### Usage

```
python3 miner-sim.py [-h] [--honest HONEST] [--colluding COLLUDING] [--rounds ROUNDS] [--graph] [--fork FORK]
```

- `-h`, `--help`: show help message and exit
- `--verbose`: Print more information about the simulation
- `--honest HONEST`: Number of honest miners (default is 3)
- `--colluding COLLUDING`: Number of colluding miners (default is 2)
- `--rounds ROUNDS`: Number of mining rounds to simulate (default is 10000)
- `--gap GAP`: Gap allowed by colluding miners, i.e. if _fork height_ + `GAP` >=
  _tip height_, continue building on the fork, else, revert to building on the
  tip (default is 5)
- `--graph`: Generate a graph of the blockchain, output to
  _blockchain_simulation.png_

### Statistics

The tool will print some statistics at the end of the simulation:

```
Fork statistics:
--------------------
  Num forks: 657
  Max depth: 30
  Abandoned blocks: 2790/10000 (27.9%)
--------------------
Miner statistics:
--------------------
  Honest miners:    82.48% confirmed
  Colluding miners: 56.28% confirmed
```

The `--verbose` flag will print additional details, about each miner and each fork:

```
Fork statistics:
--------------------
  Num forks: 1
  * From height 2 to 5
  Max depth: 4
  Abandoned blocks: 4/10 (40.0%)
--------------------
Miner statistics:
  * H1:    0 blocks mined,    0 blocks included: 0% confirmed
  * H2:    2 blocks mined,    0 blocks included: 0.0% confirmed
  * H3:    2 blocks mined,    0 blocks included: 0.0% confirmed
  * C1:    2 blocks mined,    2 blocks included: 100.0% confirmed
  * C2:    4 blocks mined,    4 blocks included: 100.0% confirmed
--------------------
  Honest miners:    0.0% confirmed
  Colluding miners: 100.0% confirmed
```

### Graph

When generating a visualization of the chain (with the `--graph` flag), the
output will look something like this:

![Example visualization](example.png)

In this visualization, blocks from honest miners have a white background, and
blocks from the colluding miners have a pink background. If a block was the tip
of the longest chain when it was mined, it will be outlined in blue.
