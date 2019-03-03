import redis
import time
import click
import multiprocessing
import sys


def worker_func(args):
    host, port, start_ts, tsrange, pipeline_size, key_index, key_format, check_only = args
    redis_client = redis.Redis(host, port)
    if check_only:
        res = redis_client.execute_command('TS.RANGE', key_format.format(index=key_index), 0, start_ts + tsrange)
        if len(res) != tsrange:
            return -1
        expected = [[long(start_ts + i), str(i)] for i in xrange(tsrange)]
        if expected != res:
            return -1
    else:
        pipe = redis_client.pipeline(tsrange)
        for i in xrange(tsrange):
            if tsrange % pipeline_size:
                pipe.execute()
            pipe.execute_command("ts.add", key_format.format(index=key_index), start_ts + i, i)
        pipe.execute()
    return tsrange


def create_compacted_key(redis, i, source, agg, bucket):
    dest = '%s_%s_%s' % (source, agg, bucket)
    redis.delete(dest)
    redis.execute_command('ts.create', dest, 'RETENTION', 0, 'CHUNK_SIZE', 360,
                          'LABELS', 'index', i, "aggregation", agg, "bucket", bucket)
    redis.execute_command('ts.createrule', source, dest, 'AGGREGATION', agg, bucket, )


@click.command()
@click.option('--host', default="localhost", help='redis host.')
@click.option('--port', type=click.INT, default=6379, help='redis port.')
@click.option('--key-count', type=click.INT, default=50, help='Number of Keys.')
@click.option('--samples', type=click.INT, default=2000, help='Number of samples per key.')
@click.option('--pool-size', type=click.INT, default=20, help='Number of workers.')
@click.option('--pipeline-size', type=click.INT, default=100, help='Number of workers.')
@click.option('--create-keys', type=click.BOOL, default=True, help='Create the keys before inserting')
@click.option('--with-compaction', type=click.BOOL, default=True, help='Create the compactions keys before inserting')
@click.option('--start-timestamp', type=click.INT, default=1551347864, help='Base timestamp for all samples')
@click.option('--key-format', type=click.STRING, default="test{{{index}}}",
              help='base key format, will be compiled with an index parameter')
@click.option('--check-only', type=click.BOOL, default=False, help='test if all keys are correcly exists in the database')
def run(host, port, key_count, samples, pool_size, create_keys, pipeline_size, with_compaction, start_timestamp,
        key_format, check_only):
    r = redis.Redis(host, port)
    print("from %s to %s" % (start_timestamp, start_timestamp + samples))

    if create_keys and not check_only:
        for i in range(key_count):
            keyname = key_format.format(index=i)
            r.delete(keyname)
            r.execute_command('ts.create', keyname, 'RETENTION', 0, 'CHUNK_SIZE', 360, 'LABELS', 'index', i)
            if with_compaction:
                create_compacted_key(r, i, keyname, 'avg', 10)
                create_compacted_key(r, i, keyname, 'avg', 60)
                create_compacted_key(r, i, keyname, 'count', 10)

    pool = multiprocessing.Pool(pool_size)
    s = time.time()
    result = pool.map(worker_func,
                      [(host, port, start_timestamp, int(samples), pipeline_size, key_index, key_format, check_only)
                       for key_index in range(key_count)])
    e = time.time()
    insert_time = e - s

    if(check_only):
        for r in result:
            if r == -1:
                print("# failed!!! not all items exists in the databse")
                sys.exit(1)
        print("# pass, all items exists in the databse")
    else:
        print("# items inserted %s:" % sum(result))
        print("took %s to insert sec, average insert time %s" % (insert_time, insert_time * 1000 / sum(result)))


if __name__ == '__main__':
    run()
