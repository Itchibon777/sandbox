import argparse
import csv
import datetime
import logging
import multiprocessing
import os
import subprocess
import sys
import urllib

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# https://www.bart.gov/about/reports/ridership
SOURCE_DIR = "http://64.111.127.166/origin-destination/"
SOURCE_FILES = [
    "date-hour-soo-dest-2011.csv.gz",
    "date-hour-soo-dest-2012.csv.gz",
    "date-hour-soo-dest-2013.csv.gz",
    "date-hour-soo-dest-2014.csv.gz",
    "date-hour-soo-dest-2015.csv.gz",
    "date-hour-soo-dest-2016.csv.gz",
    "date-hour-soo-dest-2017.csv.gz",
    "date-hour-soo-dest-2018.csv.gz",
]


def mkdir_p(path):
    if not os.path.exists(path):
        os.makedirs(path)


def _load_hourly_od(args_basename):
    args, basename = args_basename
    filename = os.path.join(DATA, basename.replace(".csv.gz", ".pkl"))
    if os.path.exists(filename):
        return torch.load(filename)

    # Download source files.
    mkdir_p(DATA)
    gz_filename = os.path.join(DATA, basename)
    if not os.path.exists(gz_filename):
        url = SOURCE_DIR + basename
        logging.debug("downloading {}".format(url))
        urllib.request.urlretrieve(url, gz_filename)
    csv_filename = gz_filename[:-3]
    assert csv_filename.endswith(".csv")
    if not os.path.exists(csv_filename):
        logging.debug("unzipping {}".format(gz_filename))
        subprocess.check_call(["gunzip", "-k", gz_filename])
    assert os.path.exists(csv_filename)

    # Convert to PyTorch.
    logging.debug("converting {}".format(csv_filename))
    start_date = datetime.datetime.strptime("2000-01-01", "%Y-%m-%d")
    stations = {}
    num_rows = sum(1 for _ in open(csv_filename))
    logging.info("Formatting {} rows".format(num_rows))
    rows = torch.empty((num_rows, 4), dtype=torch.long)
    with open(csv_filename) as f:
        for i, (date, hour, origin, destin, trip_count) in enumerate(csv.reader(f)):
            date = datetime.datetime.strptime(date, "%Y-%m-%d")
            date += datetime.timedelta(hours=int(hour))
            rows[i, 0] = int((date - start_date).total_seconds() / 3600)
            rows[i, 1] = stations.setdefault(origin, len(stations))
            rows[i, 2] = stations.setdefault(destin, len(stations))
            rows[i, 3] = int(trip_count)
            if i % 10000 == 0:
                sys.stderr.write(".")
                sys.stderr.flush()

    # Save data with metadata.
    dataset = {
        "args": args,
        "basename": basename,
        "start_date": start_date,
        "stations": stations,
        "rows": rows,
        "schema": ["time_hours", "origin", "destin", "trip_count"],
    }
    logging.debug("saving {}".format(filename))
    torch.save(dataset, filename)
    return dataset


def load_hourly_od(args=None):
    filename = os.path.join(DATA, "full-counts.pkl")
    if os.path.exists(filename):
        return torch.load(filename)

    datasets = multiprocessing.Pool().map(_load_hourly_od, [
        (args, basename)
        for basename in SOURCE_FILES
    ])

    stations = sorted(set().union(*(d["stations"].keys() for d in datasets)))
    min_time = min(int(d["rows"][:, 0].min()) for d in datasets)
    max_time = max(int(d["rows"][:, 0].max()) for d in datasets)
    num_rows = max_time - min_time + 1
    start_date = datasets[0]["start_date"] + datetime.timedelta(hours=min_time),
    logging.info("Loaded data from {} stations, {} hours"
                 .format(len(stations), num_rows))

    result = torch.zeros(num_rows, len(stations), len(stations))
    for dataset in datasets:
        part_stations = sorted(dataset["stations"], key=dataset["stations"].__getitem__)
        part_to_whole = torch.tensor(list(map(stations.index, part_stations)))
        time = dataset["rows"][:, 0] - min_time
        origin = part_to_whole[dataset["rows"][:, 1]]
        destin = part_to_whole[dataset["rows"][:, 2]]
        count = dataset["rows"][:, 3].float()
        result[time, origin, destin] = count
        dataset.clear()
    logging.info("Loaded {} shaped data of mean {:0.3g}"
                 .format(result.shape, result.mean()))

    dataset = {
        "args": args,
        "stations": stations,
        "start_date": start_date,
        "counts": result,
    }
    torch.save(dataset, filename)
    return dataset


def main(args):
    load_hourly_od(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BART data preprocessor")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(format='%(relativeCreated) 9d %(message)s',
                        level=logging.DEBUG if args.verbose else logging.INFO)
    main(args)
