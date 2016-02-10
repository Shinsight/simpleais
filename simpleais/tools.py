from collections import defaultdict
import functools
import os
import sys
import re
from contextlib import contextmanager

import click
import numpy

from . import sentences_from_source

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"


@contextmanager
def wild_disregard_for(e):
    try:
        yield
    except e:
        exit(0)


def print_sentence_source(text, file=None):
    if isinstance(text, str):
        text = [text]
    for line in text:
        if file:
            print(line, file=file)
        else:
            print(line, flush=True)


def sentences_from_sources(sources):
    if len(sources) > 0:
        for source in sources:
            for sentence in sentences_from_source(source):
                yield sentence
    else:
        for sentence in sentences_from_source(sys.stdin):
            yield sentence

@click.command()
@click.argument('sources', nargs=-1)
def cat(sources):
    for sentence in sentences_from_sources(sources):
        with wild_disregard_for(BrokenPipeError):
            print_sentence_source(sentence.text)


@click.command()
@click.argument('sources', nargs=-1)
@click.option('--mmsi', '-m', multiple=True)
@click.option('--mmsi-file', '-f')
@click.option('--type', '-t', 'sentence_type', type=int)
@click.option('--longitude', '--long', '--lon', nargs=2, type=float)
@click.option('--latitude', '--lat', nargs=2, type=float)
def grep(sources, mmsi=None, mmsi_file=None, sentence_type=None, lon=None, lat=None):
    if not mmsi:
        mmsi = []
    if mmsi_file:
        mmsi = list(mmsi)
        with open(mmsi_file, "r") as f:
            mmsi.extend([l.strip() for l in f.readlines()])
        mmsi = frozenset(mmsi)
    for sentence in sentences_from_sources(sources):
        with wild_disregard_for(BrokenPipeError):
            factors = [True]

            if len(mmsi) > 0:
                factors.append(sentence['mmsi'] in mmsi)
            if sentence_type:
                factors.append(sentence.type_id() == sentence_type)
            if lon:
                factors.append(sentence['lon'] and lon[0] < sentence['lon'] < lon[1])
            if lat:
                factors.append(sentence['lat'] and lat[0] < sentence['lat'] < lat[1])

            if functools.reduce(lambda x, y: x and y, factors):
                print_sentence_source(sentence.text)


@click.command()
@click.argument('sources', nargs=-1)
def as_text(sources):
    for sentence in sentences_from_sources(sources):
        with wild_disregard_for(BrokenPipeError):
            result = []
            if sentence.time:
                result.append(sentence.time.strftime(TIME_FORMAT))
            result.append("{:2}".format(sentence.type_id()))
            result.append("{:9}".format(str(sentence['mmsi'])))
            if sentence['lat']:
                result.append("{:9.4f} {:9.4f}".format(sentence['lat'], sentence['lon']))
            elif sentence.type_id() == 5:
                result.append("{}->{}".format(sentence['shipname'], sentence['destination']))

            print(" ".join(result))


@click.command()
@click.argument('source', nargs=1)
@click.argument('dest', nargs=1, required=False)
def burst(source, dest):
    if not dest:
        dest = source
    writers = {}
    fname, ext = os.path.splitext(dest)

    for sentence in sentences_from_source(source):
        mmsi = sentence['mmsi']
        if not mmsi:
            mmsi = 'other'
        if mmsi not in writers:
            writers[mmsi] = open("{}-{}{}".format(fname, mmsi, ext), "wt")
        print_sentence_source(sentence.text, writers[mmsi])

    for writer in writers.values():
        writer.close()


class Fields:
    def __init__(self):
        self.values = {}

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        value = value.strip()
        if key and value and len(value) > 0:
            self.values[key] = value

    def __iter__(self):
        return self.values.__iter__()


class SenderInfo:
    def __init__(self):
        self.mmsi = None
        self.sentence_count = 0
        self.type_counts = defaultdict(int)
        self.fields = Fields()

    def add(self, sentence):
        if not self.mmsi:
            self.mmsi = sentence['mmsi']
        self.sentence_count += 1
        self.type_counts[sentence.type_id()] += 1
        if sentence.type_id() == 5:
            self.fields['shipname'] = sentence['shipname']
            self.fields['destination'] = sentence['destination']

    def report(self):
        print("{}:".format(self.mmsi))
        print("    sentences: {}".format(self.sentence_count))
        type_text = ["{}: {}".format(t, self.type_counts[t]) for t in (sorted(self.type_counts))]
        print("        types: {}".format(", ".join(type_text)))
        for field in sorted(self.fields):
            print("  {:>11s}: {}".format(field, self.fields[field]))


class MaxMin:
    def __init__(self, starting=None):
        self.min = self.max = starting

    def valid(self):
        return self.min is not None and self.min is not None

    def add(self, value):
        if not self.valid():
            self.min = self.max = value
            return
        if value > self.max:
            self.max = value
        if value < self.min:
            self.min = value


class GeoInfo:
    def __init__(self):
        self.lon = MaxMin()
        self.lat = MaxMin()

    def add(self, point):
        self.lon.add(point[0])
        self.lat.add(point[1])

    def report(self, indent=""):
        print("{}    top left: {}, {}".format(indent, self.lat.max, self.lon.min))
        print("{}bottom right: {}, {}".format(indent, self.lat.min, self.lon.max))

    def __str__(self, *args, **kwargs):
        return "GeoInfo(latmin={}, latmax={}, lonmin={}, lonmax={})".format(self.lat.min, self.lat.max,
                                                                            self.lon.min, self.lon.max)

    def valid(self):
        return self.lon.valid() and self.lat.valid()


class SentencesInfo:
    def __init__(self):
        self.sentence_count = 0
        self.type_counts = defaultdict(int)
        self.sender_counts = defaultdict(int)
        self.geo_info = GeoInfo()


    def add(self, sentence):
        self.sentence_count += 1
        self.type_counts[sentence.type_id()] += 1
        self.sender_counts[sentence['mmsi']] += 1
        loc = sentence.location()
        if loc:
            self.geo_info.add(loc)

    def report(self):
        print("Found {} senders in {} sentences.".format(len(self.sender_counts), self.sentence_count))
        print("   type counts:")
        for i in sorted(self.type_counts):
            print("                {:2d} {:8d}".format(i, self.type_counts[i]))
        print()
        self.geo_info.report("  ")


class Bucketer:
    """Given min, max, and buckets, buckets values"""

    def __init__(self, min_val, max_val, bucket_count):
        self.min_val = min_val
        self.max_val = max_val
        self.bucket_count = bucket_count
        self.max_buckets = bucket_count - 1
        if self.min_val == self.max_val:
            self.bins = numpy.linspace(min_val - 1, max_val + 1, bucket_count + 1)
        else:
            self.bins = numpy.linspace(min_val, max_val + sys.float_info.epsilon, bucket_count + 1)

    def bucket(self, value):
        result = numpy.digitize(value, self.bins) - 1

        # this shouldn't be necessary, but it somehow is
        if result > self.max_buckets:
            return self.max_buckets
        return result

    def __str__(self, *args, **kwargs):
        return "Bucketer({}, {}, {}, {})".format(self.min_val, self.max_val, self.bucket_count, self.bins)


class DensityMap:
    def __init__(self, width=60, height=20, indent=""):
        self.width = width
        self.height = height
        self.indent = indent
        self.geo_info = GeoInfo()
        self.points = []

    def add(self, point):
        self.points.append(point)
        self.geo_info.add(point)

    def to_counts(self):
        # noinspection PyUnusedLocal
        results = [[0 for ignored in range(self.width)] for ignored in range(self.height)]
        if self.geo_info.valid():
            xb = Bucketer(self.geo_info.lon.min, self.geo_info.lon.max, self.width)
            yb = Bucketer(self.geo_info.lat.min, self.geo_info.lat.max, self.height)
            for lon, lat in self.points:
                x = xb.bucket(lon)
                y = self.height - 1 - yb.bucket(lat)
                results[y][x] += 1
        return results

    def to_text(self):
        counts = self.to_counts()

        max_count = max([max(l) for l in counts])

        def value_to_text(value):
            if value == 0:
                return " "
            return str(int((9.99999) * value / max_count))

        output = []
        output.append("{}+{}+".format(self.indent, "-" * self.width))
        for row in counts:
            output.append("{}|{}|".format(self.indent, "".join([value_to_text(col) for col in row])))
        output.append("{}+{}+".format(self.indent, "-" * self.width))
        return output

    def show(self):
        print("\n".join(self.to_text()))


@click.command()
@click.argument('sources', nargs=-1)
@click.option('--individual', '-i', is_flag=True)
@click.option('--map', '-m', "show_map", is_flag=True)
def info(sources, individual, show_map):
    sentences_info = SentencesInfo()
    sender_info = defaultdict(SenderInfo)
    map_info = DensityMap()

    for sentence in sentences_from_sources(sources):
        sentences_info.add(sentence)
        if show_map:
            loc = sentence.location()
            if loc:
                map_info.add(loc)
        if individual:
            sender_info[sentence['mmsi']].add(sentence)

    with wild_disregard_for(BrokenPipeError):
        sentences_info.report()
        if show_map:
            map_info.show()

        if individual:
            for mmsi in sorted(sender_info):
                sender_info[mmsi].report()


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


@click.command()
@click.argument('sources', nargs=-1)
@click.option('--bits', '-b', is_flag=True)
def dump(sources, bits):
    sentence_count = 0
    for sentence in sentences_from_sources(sources):
        with wild_disregard_for(BrokenPipeError):
            if sentence_count != 0:
                print()
            sentence_count += 1
            print("Sentence {}:".format(sentence_count))
            if sentence.time:
                print("          time: {}".format(sentence.time.strftime(TIME_FORMAT)))
            for t in sentence.text:
                print("          text: {}".format(re.search("!.*", t).group(0)))
            print("        length: {}".format(len(sentence.message_bits())))
            if bits:
                bit_lumps = list(chunks(str(sentence.message_bits()), 6))
                groups = chunks(bit_lumps, 8)
                pos = 0
                print("         check: {}".format(", ".join([str(c) for c in sentence.checksum_valid])))
                print("          bits: {:3d} {}".format(pos, " ".join(groups.__next__())))
                for group in groups:
                    pos += 48
                    print("          bits: {:3d} {}".format(pos, " ".join(group)))

            for field in sentence.fields():
                value = '-'
                if field.valid():
                    value = field.value()
                if bits:
                    print("  {:>12}: {} ({})".format(field.name(), value, field.bits()))
                else:
                    print("  {:>12}: {}".format(field.name(), value))
