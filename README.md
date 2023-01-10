# Ping tracker

This project provides a simple Python script to send ICMP echo requests (a.k.a. `ping`) to a list of network devices and store the results in a simple database. This project was developed to track the online status of hospital devices in order to find devices which could be potentially shut down during off-hours in order to save energy.

# Installation
The script relies on the [`fping`](https://github.com/schweikert/fping) utility, which can be easily installed on recent Debian/Ubuntu distributions using
```bash
sudo apt-get install -y fping
```
or by following the official installation instructions. You might need to set additional file capabilities in order to be able to run the program as non-root, which can be achieved by issuing the following command on a linux machine:
```bash
sudo setcap cap_net_raw+ep `which fping`
```
To download this script, setup/activate a virtual environment and install all dependencies use:
```bash
git clone https://github.com/mtmey/ping_tracker.git
cd ping_tracker
python3 -m venv .venv --prompt "ping_tracker_venv"
source .venv/bin/activate
pip install -r requirements.txt
```
You might need to install `python3` (tested on Python 3.9) and python's `venv` package.

# Usage
The script is intended to be used on the command line. For an overview of its functionality use:
```bash
python poll.py --help
```
The list of hosts which should be scanned can either be passed with one (or multiple) plain text files with one hostname or IP address per line, or as a [sqlite3](https://www.sqlite.org/index.html) database with a table named `hosts` (at least two columns are expected: `id` with an unique integer id for the host and `hostname` with the IP address or DNS resolvable hostname).

# Examples
To query the reachability of the following two hosts, let's consider the following plain text file (`test_hosts.txt`):
```
google.com
1.1.1.1
```
The hosts are queried by issuing:
```bash
python poll.py --table --csv=results.csv test_hosts.txt
```
which will result in the following output:
```
            reachable          ts
machine                          
google.com       True  1673391318
1.1.1.1          True  1673391318

✅ Saved table to results.csv
```
The contents of `results.csv` are:
```csv
machine,reachable,ts
google.com,True,1673391457
1.1.1.1,True,1673391457
```
To append results to an existing CSV file, add the `--append` option to above command.
