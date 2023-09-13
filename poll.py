#!/bin/env python

import re
import time
import sqlite3
import argparse
import warnings
import subprocess as sp
from typing import Optional
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
from rich import get_console


ts_schema = '''CREATE TABLE IF NOT EXISTS ts (
timestamp INTEGER NOT NULL,
host_id INTEGER NOT NULL,
online INTEGER CHECK (online IN (0, 1)),
latency_ms REAL,
FOREIGN KEY(host_id) REFERENCES hosts(id) ON DELETE SET NULL ON UPDATE CASCADE
)'''


_console = get_console()


def rprint(*args, highlight=False, **kwargs):
    _console.print(*args, highlight=highlight, **kwargs)


class FPingException(Exception):
    # see the fping man page under Diagnostics
    ret_codes = {0: 'all hosts are reachable',  # not acutally an error
                 1: 'some hosts are unreachable',  # not acutally an error
                 2: 'some IP addresses or hostnames were not found',
                 3: 'invalid command line arguments',
                 4: 'system call failure'}

    def __init__(self, exit_code):
        self.exit_code = exit_code
        self.message = self.ret_codes[exit_code]
        super().__init__(f'fping returned with an error: {self.message}')


class FPingNotFound(Exception):
    """ Raised if fping could not be found in $PATH """
    pass


def check_hosts(hosts: list[str], timeout_ms: Optional[int] = 50, reverse_lookup: bool = False,
                add_elapsed: bool = False, ipv4_only: bool = True, include_failed_dns: bool = False) -> pd.DataFrame:
    """
    Pings all specified `hosts` using the `fping` command line utility, and returns a DataFrame with the following columns:

    - `machine`: The hostname or IP address of the machine that was pinged.
    - `reachable`: A boolean indicating whether the host was reachable or not. This column will be `NaN` for hosts which are not resolvable by the DNS server if `include_failed_dns` is `True`.
    - `ts`: A unix timestamp in seconds (since 1970-01-01) indicating the time at which the ping was performed.
    - `ping` (optional): The ping latency in milliseconds (float). This column will be present only if the `add_elapsed` argument is set to `True`, and will be `NaN` for non-reachable hosts.

    Args:
        hosts (list[str]): A list of hostnames or IP addresses to ping.
        timeout_ms (int, optional): Time in milliseconds before a ping times out. If `None`, use `fping`s default value. Defaults to 50.
        reverse_lookup (bool, optional): Equivalent to the `-d` option of the `fping` command (DNS reverse lookup hostname before pinging it). Cannot be used if `include_failed_dns` is set. Defaults to `False`.
        add_elapsed (bool, optional): If `True`, adds a fourth column `ping` with the ping latency in milliseconds (float). Will be `NaN` for non-reachable hosts. Defaults to `False`.
        ipv4_only (bool, optional): If `True`, instruct `fping` to restrict name resolution and IPs to IPv4 only, useful if no IPv6 hosts are in the network. Defaults to `True`.
        include_failed_dns (bool, optional): If `True`, hosts which are not resolvable by the DNS server will be included in the result and `reachable` will be `NaN`.
                                             If `False`, those hosts will not be included in the result and a warning will be printed instead. Defaults to `False`.

    Returns:
        pd.DataFrame: A DataFrame with `machine` as the index column and the status of each host as a row (returned columns: see above).
    """
    cmd = ['fping']
    if (n_dup := len(hosts) - len(set(hosts))) > 0:
        warnings.warn(f'{n_dup} host{"s are" if n_dup > 1 else " is"} duplicated, make sure to include each host only once')
    if timeout_ms is not None:
        cmd.append(f'--timeout={timeout_ms:d}')
    if reverse_lookup:
        cmd.append('-d')
    if add_elapsed:
        cmd.append('-e')
    if ipv4_only:
        cmd.append('--ipv4')
    if not sp.run(['which', 'fping'], capture_output=True).returncode == 0:
        raise FPingNotFound()
    call = sp.run(cmd + hosts, capture_output=True)
    if call.returncode >= 3:  # 3 is invalid command line options (should not happen), 4 is system call error
        raise FPingException(call.returncode)
    pattern = r'(.*?) is ([a-z]+)'
    if add_elapsed:
        pattern += r'(?: \((\d+.\d+) ms\))?'  # non-capturing group at the outermost level
    stati = re.findall(pattern, call.stdout.decode())  # output is either '{host} is alive' or '{host} is unreachable'
    cols = ['machine', 'reachable']
    if add_elapsed:
        cols.append('ping')
    df = pd.DataFrame(stati, columns=cols)
    df['reachable'] = df['reachable'].replace({'alive': True, 'unreachable': False})
    now = int(pd.Timestamp.now(tz='UTC').value / 1e9)
    df['ts'] = now
    df = df.set_index('machine')
    if add_elapsed:
        df['ping'] = df['ping'].replace('', np.nan).astype('float')
    if (n_err := len(hosts) - len(df)) != 0:  # could also use: call.returncode == 2:  # return code for: some IP addresses were not found
        if reverse_lookup and include_failed_dns:
            warnings.warn('Arguments `include_failed_dns` and `reverse_lookup` are mutually exclusive. Hosts with failed DNS lookup are not included in the result.')
            include_failed_dns = False
        if include_failed_dns:
            missing_hosts = [x for x in hosts if x not in df.index]  # can not use sets here because of potential duplicates
            missing_df = pd.DataFrame({'machine': missing_hosts, 'reachable': pd.NA, 'ts': now})
            missing_df['reachable'] = missing_df['reachable'].astype('boolean')  # convert to nullable boolean
            missing_df = missing_df.set_index('machine')
            df = pd.concat((df, missing_df))  # add non-resolvable hosts at the end, ping column will be NaN
        else:
            warnings.warn(f'`fping` did not return a result for {n_err:d} host{"s" if n_err > 1 else ""}.')
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Pings a list of hosts to check if they are online',
                                     epilog='Written by Manfred T. Meyer, University Hospital Basel. If you use this tool, please cite https://pubs.rsna.org/doi/10.1148/radiol.230162')
    parser.add_argument('hosts', nargs='*', help='path(s) to text files with hosts to ping, one hostname or IP address per line')
    parser.add_argument('--timeout', '-t', type=int, default=50, help='ping timeout in milliseconds (integer, default: 50)')
    parser.add_argument('--elapsed', '-e', action='store_true', help='record elapsed time in milliseconds (ping latency)')
    parser.add_argument('--exclude_failed', '-f', action='store_true', help='do not include hosts with failed DNS resolution in the result')
    parser.add_argument('--reverse', '-r', action='store_true', help='use reverse DNS lookup on provided IPs and hostnames')
    parser.add_argument('--ipv6', '-6', action='store_true', help='also resolve IPv6 addresses (by default, only IPv4 addresses are DNS resolved)')
    parser.add_argument('--summary', '-s', action='store_true', help='show a summary after completion (displayed by default if `--table` option is not set)')
    parser.add_argument('--table', '-v', action='store_true', help='print a table with all results to stdout (verbose)')
    parser.add_argument('--csv', '-c', type=Path, default=None, help='CSV output file path, will overwrite any contents if the `--append` option is net set')
    parser.add_argument('--sqlite', '-q', type=Path, default=None, help='SQLite3 database file path (may not specify any additional `hosts`). Hostnames are read from table `hosts` (needs an `id` and `hostname` column`), results are witten/appended to table `ts`.')
    parser.add_argument('--append', '-a', action='store_true', help='append results to an existing CSV file without header, used in conjunction with `--csv`')

    args = parser.parse_args()

    # collect all hosts we need to ping
    hosts = []
    if args.sqlite is not None:
        if len(args.hosts) > 0:
            rprint(f'[red b]ERROR[/]: Must not pass additional hosts if the [dim]--sqlite[/] option is used.')
            exit(1)
        with open(args.sqlite, 'rb') as fp:
            if not fp.read(16) == b'SQLite format 3\x00':  # SQLite header string according to https://www.sqlite.org/fileformat.html#the_database_header
                rprint(f'[red b]ERROR[/]: Please pass a path to a valid SQLite 3 database after the [dim]--sqlite[/] argument.')
                exit(1)
        db = sqlite3.connect(args.sqlite, timeout=60.)  # use a high timeout since redash can block the db resulting in OperationalError
        hosts_db = pd.read_sql_query('select * from hosts', db, index_col='id', parse_dates={'installation_date': 's', 'last_change_date': 's'})  # add additional date columns here
        hosts.extend(hosts_db['hostname'].to_list())
    else:
        for path in args.hosts:
            hosts.extend(pd.read_csv(path, header=None)[0].to_list())  # one host per line in each text file
    
    # ping all hosts
    start = time.time()
    try:
        df = check_hosts(hosts, timeout_ms=args.timeout, reverse_lookup=args.reverse, add_elapsed=args.elapsed,
                         ipv4_only=not args.ipv6, include_failed_dns=not args.exclude_failed)
    except FPingException as err:
        rprint(f'[red b]ERROR[/]: fping returned with: {err.message}')
        if err.exit_code == 4:  # system call failure, usually because fping needs to have the right file capabilities to be run as non-priviledged user
            rprint('[red b]ERROR[/]: make sure fping has the right permissions (i.e. try "[dim]sudo setcap cap_net_raw+ep `which fping`[dim]" on linux)')
        exit(1)
    except FPingNotFound:
        rprint('[red b]ERROR[/]: fping was not found. Please install it first and make sure it is executable and in $PATH.')
        exit(1)
    end = time.time()

    if args.table:  # print a nicely indented table if specified
        print(df.to_string() + '\n')
    if args.summary or not args.table:  # default: print a short summary message
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rprint(f'[dim]\[{now}][/] [b green]{df.reachable.sum()}[/] hosts are up, [b red]{(~df.reachable.fillna(False)).sum()}[/] are down [dim](took {end-start:.1f} s)[/]')
    if args.sqlite is not None:  # store in the database if specified
        df_sqlite = df.join(hosts_db.reset_index().set_index('hostname'), on='machine').dropna(subset='id').astype({'id': 'int'})
        df_sqlite = df_sqlite.rename(columns={'ts': 'timestamp', 'id': 'host_id', 'reachable': 'online', 'ping': 'latency_ms'})
        if 'latency_ms' not in df_sqlite.columns:  # TODO: make this column optional
            df_sqlite['latency_ms'] = np.nan
        cur = db.cursor()
        cur.execute('PRAGMA foreign_keys=1;')  # allow only valid host ids
        cur.execute('PRAGMA ignore_check_constraints=0;')  # allow only boolean values (and NULL) in the `online` column
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='ts';")  # check if `ts` table exists
        if cur.fetchone()[0] != 1:  # 0 if table does not exist
            cur.execute(ts_schema)  # the schema arg from DataFrame's to_sql command does not seem to work here (check constraints etc. are dropped)
        # cur.executemany('INSERT INTO ts (timestamp, host_id, online, latency_ms) VALUES (?, ?, ?, ?);', ts_replace[['timestamp', 'host_id', 'online', 'latency_ms']].fillna(np.nan).replace([np.nan], [None]).to_records(index=False).tolist())
        df_sqlite[['timestamp', 'host_id', 'online', 'latency_ms']].to_sql('ts', db, if_exists='append', index=False, schema=ts_schema)  # dtype={'timestamp': 'INTEGER', 'host_id': 'INTEGER', 'online': 'INTEGER', 'latency_ms': 'REAL'})  # is slightly slower than executemany
        db.execute('VACUUM;')
        db.execute('PRAGMA optimize;')  # should be run regularly according to sqlite docs
        db.commit()
        db.close()
    if args.csv is not None:  # output to a CSV file if specified
        mode = 'a' if args.append else 'w'
        needs_header = (not args.csv.is_file() or mode == 'w')
        df.to_csv(args.csv, mode=mode, header=needs_header)
        rprint(f':white_check_mark: Saved table to [u b]{args.csv}[/]{" in append mode" if mode == "a" else ""}')
