import os
import redis
import pytest
import time
import __builtin__
import math
from rmtest import ModuleTestCase

class RedisTimeseriesTests(ModuleTestCase(os.path.dirname(os.path.abspath(__file__)) + '/../redistimeseries.so')):
    def _get_ts_info(self, redis, key):
        info = redis.execute_command('TS.INFO', key)
        return dict([(info[i], info[i+1]) for i in range(0, len(info), 2)])

    @staticmethod
    def _insert_data(redis, key, start_ts, samples_count, value):
        """
        insert data to key, starting from start_ts, with 1 sec interval between them
        :param redis: redis connection
        :param key: name of time_series
        :param start_ts: beginning of time series
        :param samples_count: number of samples
        :param value: could be a list of samples_count values, or one value. if a list, insert the values in their
        order, if not, insert the single value for all the timestamps
        """
        for i in range(samples_count):
            value_to_insert = value[i] if type(value) == list else value
            assert redis.execute_command('TS.ADD', key, start_ts + i, value_to_insert)

    def _insert_agg_data(self, redis, key, agg_type):
        agg_key = '%s_agg_%s_10' % (key, agg_type)

        assert redis.execute_command('TS.CREATE', key)
        assert redis.execute_command('TS.CREATE', agg_key)
        assert redis.execute_command('TS.CREATERULE', key, agg_key, "AGGREGATION", agg_type, 10)

        values = (31, 41, 59, 26, 53, 58, 97, 93, 23, 84)
        for i in range(10, 50):
            assert redis.execute_command('TS.ADD', key, i, i // 10 * 100 + values[i % 10])

        return agg_key

    @staticmethod
    def _get_series_value(ts_key_result):
        """
        Get only the values from the time stamp series
        :param ts_key_result: the output of ts.range command (pairs of timestamp and value)
        :return: float values of all the values in the series
        """
        return [float(value[1]) for value in ts_key_result]

    @staticmethod
    def _calc_downsampling_series(values, bucket_size, calc_func):
        """
        calculate the downsampling series given the wanted calc_func, by applying to calc_func to all the full buckets
        and to the remainder bucket
        :param values: values of original series
        :param bucket_size: bucket size for downsampling
        :param calc_func: function that calculates the wanted rule, for example min/sum/avg
        :return: the values of the series after downsampling
        """
        series = []
        for i in range(0, int(math.ceil(len(values) / float(bucket_size)))):
            curr_bucket_size = bucket_size
            # we don't have enough values for a full bucket anymore
            if (i + 1) * bucket_size > len(values):
                curr_bucket_size = len(values) - i
            series.append(calc_func(values[i * bucket_size: i * bucket_size + curr_bucket_size]))
        return series

    def calc_rule(self, rule, values, bucket_size):
        """
        Calculate the downsampling with the given rule
        :param rule: 'avg' / 'max' / 'min' / 'sum' / 'count'
        :param values: original series values
        :param bucket_size: bucket size for downsampling
        :return: the values of the series after downsampling
        """
        if rule == 'avg':
            return self._calc_downsampling_series(values, bucket_size, lambda x: float(sum(x)) / len(x))
        elif rule in ['sum', 'max', 'min']:
            return self._calc_downsampling_series(values, bucket_size, getattr(__builtin__, rule))
        elif rule == 'count':
            return self._calc_downsampling_series(values, bucket_size, len)

    def test_sanity(self):
        start_ts = 1511885909L
        samples_count = 1500
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester', 'RETENTION', '0', 'CHUNK_SIZE', '360', 'LABELS', 'name',
                                     'brown', 'color', 'pink')
            self._insert_data(r, 'tester', start_ts, samples_count, 5)

            expected_result = [[start_ts+i, str(5)] for i in range(samples_count)]
            actual_result = r.execute_command('TS.range', 'tester', start_ts, start_ts + samples_count)
            assert expected_result == actual_result

            expected_result = {'chunkCount': math.ceil((samples_count + 1) / 360.0),
              'labels': [['name', 'brown'], ['color', 'pink']],
              'lastTimestamp': start_ts + samples_count - 1,
              'maxSamplesPerChunk': 360L,
              'retentionSecs': 0L,
              'rules': []}
            actual_result = self._get_ts_info(r, 'tester')
            assert expected_result == actual_result


    def test_rdb(self):
        start_ts = 1511885909L
        samples_count = 1500
        data = None
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester', 'RETENTION', '0', 'CHUNK_SIZE', '360', 'LABELS', 'name', 'brown', 'color', 'pink')
            assert r.execute_command('TS.CREATE', 'tester_agg_avg_10')
            assert r.execute_command('TS.CREATE', 'tester_agg_max_10')
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_avg_10', 'AGGREGATION', 'AVG', 10)
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'MAX', 10)
            self._insert_data(r, 'tester', start_ts, samples_count, 5)
            data = r.execute_command('dump', 'tester')

        with self.redis() as r:
            r.execute_command('RESTORE', 'tester', 0, data)
            expected_result = [[start_ts+i, str(5)] for i in range(samples_count)]
            actual_result = r.execute_command('TS.range', 'tester', start_ts, start_ts + samples_count)
            assert expected_result == actual_result

            expected_result = {'chunkCount': math.ceil((samples_count + 1) / 360.0),
                               'labels': [['name', 'brown'], ['color', 'pink']],
                               'lastTimestamp': 1511887408L,
                               'maxSamplesPerChunk': 360L,
                               'retentionSecs': 0L,
                               'rules': [['tester_agg_avg_10', 10L, 'AVG'], ['tester_agg_max_10', 10L, 'MAX']]}
            actual_result = self._get_ts_info(r, 'tester')
            assert expected_result == actual_result

    def test_rdb_aggregation_context(self):
        """
        Check that the aggregation context of the rules is saved in rdb. Write data with not a full bucket,
        then save it and restore, add more data to the bucket and check the rules results considered the previous data
        that was in that bucket in their calculation. Check on avg and min, since all the other rules use the same
        context as min.
        """
        start_ts = 3
        samples_count = 4  # 1 full bucket and another one with 1 value
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            assert r.execute_command('TS.CREATE', 'tester_agg_avg_3')
            assert r.execute_command('TS.CREATE', 'tester_agg_min_3')
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_avg_3', 'AGGREGATION', 'AVG', 3)
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_min_3', 'AGGREGATION', 'MIN', 3)
            self._insert_data(r, 'tester', start_ts, samples_count, range(samples_count))
            data_tester = r.execute_command('dump', 'tester')
            data_avg_tester = r.execute_command('dump', 'tester_agg_avg_3')
            data_min_tester = r.execute_command('dump', 'tester_agg_min_3')

        with self.redis() as r:
            r.execute_command('RESTORE', 'tester', 0, data_tester)
            r.execute_command('RESTORE', 'tester_agg_avg_3', 0, data_avg_tester)
            r.execute_command('RESTORE', 'tester_agg_min_3', 0, data_min_tester)
            assert r.execute_command('TS.ADD', 'tester', start_ts + samples_count, samples_count)
            # if the aggregation context wasn't saved, the results were considering only the new value added
            expected_result_avg = [[start_ts, '1'], [start_ts + 3, '3.5']]
            expected_result_min = [[start_ts, '0'], [start_ts + 3, '3']]
            actual_result_avg = r.execute_command('TS.range', 'tester_agg_avg_3', start_ts, start_ts + samples_count)
            assert actual_result_avg == expected_result_avg
            actual_result_min = r.execute_command('TS.range', 'tester_agg_min_3', start_ts, start_ts + samples_count)
            assert actual_result_min == expected_result_min

    def test_sanity_pipeline(self):
        start_ts = 1488823384L
        samples_count = 1500
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            with r.pipeline(transaction=False) as p:
                p.set("name", "danni")
                self._insert_data(p, 'tester', start_ts, samples_count, 5)
                p.execute()
            expected_result = [[start_ts+i, str(5)] for i in range(samples_count)]
            actual_result = r.execute_command('TS.range', 'tester', start_ts, start_ts + samples_count)
            assert expected_result == actual_result

    def test_range_query(self):
        start_ts = 1488823384L
        samples_count = 1500
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            self._insert_data(r, 'tester', start_ts, samples_count, 5)

            expected_result = [[start_ts+i, str(5)] for i in range(100, 151)]
            actual_result = r.execute_command('TS.range', 'tester', start_ts+100, start_ts + 150)
            assert expected_result == actual_result

    def test_range_with_agg_query(self):
        start_ts = 1488823384L
        samples_count = 1500
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            self._insert_data(r, 'tester', start_ts, samples_count, 5)

            expected_result = [[1488823000L, '116'], [1488823500L, '500'], [1488824000L, '500'], [1488824500L, '384']]
            actual_result = r.execute_command('TS.range', 'tester', start_ts, start_ts + samples_count, 'AGGREGATION',
                                              'count', 500)
            assert expected_result == actual_result

    def test_compaction_rules(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester', 'CHUNK_SIZE', '360')
            assert r.execute_command('TS.CREATE', 'tester_agg_max_10')
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'avg', 10)

            start_ts = 1488823384L
            samples_count = 1500
            self._insert_data(r, 'tester', start_ts, samples_count, 5)

            actual_result = r.execute_command('TS.RANGE', 'tester_agg_max_10', start_ts, start_ts + samples_count)

            assert len(actual_result) == samples_count/10

            info_dict = self._get_ts_info(r, 'tester')
            assert info_dict == {'chunkCount': math.ceil((samples_count + 1) / 360.0),
                                 'lastTimestamp': start_ts + samples_count -1,
                                 'maxSamplesPerChunk': 360L,
                                 'retentionSecs': 0L,
                                 'labels': [],
                                 'rules': [['tester_agg_max_10', 10L, 'AVG']]}
    
    def test_create_compaction_rule_without_dest_series(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            with pytest.raises(redis.ResponseError) as excinfo:
                assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'MAX', 10)

    def test_create_compaction_rule_twice(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            assert r.execute_command('TS.CREATE', 'tester_agg_max_10')
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'MAX', 10)
            with pytest.raises(redis.ResponseError) as excinfo:
                assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'MAX', 10)

    def test_create_compaction_rule_and_del_dest_series(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            assert r.execute_command('TS.CREATE', 'tester_agg_max_10')
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'AVG', 10)
            assert r.delete('tester_agg_max_10')

            start_ts = 1488823384L
            samples_count = 1500
            self._insert_data(r, 'tester', start_ts, samples_count, 5)

    def test_delete_rule(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            assert r.execute_command('TS.CREATE', 'tester_agg_max_10')
            assert r.execute_command('TS.CREATERULE', 'tester', 'tester_agg_max_10', 'AGGREGATION', 'AVG', 10)

            with pytest.raises(redis.ResponseError) as excinfo:
                assert r.execute_command('TS.DELETERULE', 'tester', 'non_existent')

            assert len(self._get_ts_info(r, 'tester')['rules']) == 1
            assert r.execute_command('TS.DELETERULE', 'tester', 'tester_agg_max_10')
            assert len(self._get_ts_info(r, 'tester')['rules']) == 0

    def test_empty_series(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            assert r.execute_command('DUMP', 'tester')

    def test_incrby_reset(self):
        with self.redis() as r:
            r.execute_command('ts.create', 'tester')

            i = 0
            time_bucket = 10
            start_time = int(time.time())
            start_time = start_time - start_time % time_bucket
            while i < 1000:
                i += 1
                r.execute_command('ts.incrby', 'tester', '1', 'RESET', time_bucket)

            assert r.execute_command('TS.RANGE', 'tester', 0, int(time.time())) == [[start_time, '1000']]

    def test_incrby(self):
        with self.redis() as r:
            r.execute_command('ts.create', 'tester')

            start_incr_time = int(time.time())
            for i in range(20):
                r.execute_command('ts.incrby', 'tester', '5')

            time.sleep(1)
            start_decr_time = int(time.time())
            for i in range(20):
                r.execute_command('ts.decrby', 'tester', '1.5')

            assert r.execute_command('TS.RANGE', 'tester', 0, int(time.time())) == [[start_incr_time, '100'], [start_decr_time, '70']]

    def test_agg_min(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'min')

            expected_result = [[10, '123'], [20, '223'], [30, '323'], [40, '423']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_max(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'max')

            expected_result = [[10, '197'], [20, '297'], [30, '397'], [40, '497']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_avg(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'avg')

            expected_result = [[10, '156.5'], [20, '256.5'], [30, '356.5'], [40, '456.5']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_sum(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'sum')

            expected_result = [[10, '1565'], [20, '2565'], [30, '3565'], [40, '4565']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_count(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'count')

            expected_result = [[10, '10'], [20, '10'], [30, '10'], [40, '10']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_first(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'first')

            expected_result = [[10, '131'], [20, '231'], [30, '331'], [40, '431']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_last(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'last')

            expected_result = [[10, '184'], [20, '284'], [30, '384'], [40, '484']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_agg_range(self):
        with self.redis() as r:
            agg_key = self._insert_agg_data(r, 'tester', 'range')

            expected_result = [[10, '74'], [20, '74'], [30, '74'], [40, '74']]
            actual_result = r.execute_command('TS.RANGE', agg_key, 10, 50)
            assert expected_result == actual_result

    def test_downsampling_rules(self):
        """
        Test downsmapling rules - avg,min,max,count,sum with 4 keys each.
        Downsample in resolution of:
        1sec (should be the same length as the original series),
        3sec (number of samples is divisible by 10),
        10s (number of samples is not divisible by 10),
        1000sec (series should be empty since there are not enough samples)
        Insert some data and check that the length, the values and the info of the downsample series are as expected.
        """
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
            rules = ['avg', 'sum', 'count', 'max', 'min']
            resolutions = [1, 3, 10, 1000]
            for rule in rules:
                for resolution in resolutions:
                    assert r.execute_command('TS.CREATE', 'tester_{}_{}'.format(rule, resolution))
                    assert r.execute_command('TS.CREATERULE', 'tester', 'tester_{}_{}'.format(rule, resolution),
                                             'AGGREGATION', rule, resolution)

            start_ts = 0
            samples_count = 501
            end_ts = start_ts + samples_count
            values = range(samples_count)
            self._insert_data(r, 'tester', start_ts, samples_count, values)

            for rule in rules:
                for resolution in resolutions:
                    actual_result = r.execute_command('TS.RANGE', 'tester_{}_{}'.format(rule, resolution),
                                                      start_ts, end_ts)
                    assert len(actual_result) == math.ceil(samples_count / float(resolution))
                    expected_result = self.calc_rule(rule, values, resolution)
                    assert self._get_series_value(actual_result) == expected_result
                    # last time stamp should be the beginning of the last bucket
                    assert self._get_ts_info(r, 'tester_{}_{}'.format(rule, resolution))['lastTimestamp'] == \
                                            (samples_count - 1) - (samples_count - 1) % resolution

    def test_automatic_timestamp(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester')
        curr_time = int(time.time())
        r.execute_command('TS.ADD', 'tester', '*', 1)
        result = r.execute_command('TS.RANGE', 'tester', 0, int(time.time()))
        # test time difference is not more than 1 second
        assert result[0][0] - curr_time <= 1

    def test_add_create_key(self):
        with self.redis() as r:
            ts = time.time()
            assert r.execute_command('TS.ADD', 'tester1', str(int(ts)), str(ts), 'RETENTION', '666', 'LABELS', 'name', 'blabla')
            assert r.execute_command('TS.INFO', 'tester1') == [
                'lastTimestamp',
                long(ts),
                'retentionSecs',
                666L,
                'chunkCount',
                1L,
                'maxSamplesPerChunk',
                360L,
                'labels',
                [
                    ['name',
                    'blabla']
                ],
                'rules',
                []
            ]

            assert r.execute_command('TS.ADD', 'tester2', str(int(ts)), str(ts), 'LABELS', 'name', 'blabla2', 'location', 'earth')
            assert r.execute_command('TS.INFO', 'tester2') == [
                'lastTimestamp',
                long(ts),
                'retentionSecs',
                0L,
                'chunkCount',
                1L,
                'maxSamplesPerChunk',
                360L,
                'labels',
                [
                    [
                        'name',
                        'blabla2'
                     ],
                    [
                        'location',
                        'earth'
                    ]
                ],
                'rules',
                []
            ]

    def test_range_by_labels(self):
        start_ts = 1511885909L
        samples_count = 50

        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester1', 'LABELS', 'name', 'bob', 'class', 'middle', 'generation', 'x')
            assert r.execute_command('TS.CREATE', 'tester2', 'LABELS', 'name', 'rudy', 'class', 'junior', 'generation', 'x')
            assert r.execute_command('TS.CREATE', 'tester3', 'LABELS', 'name', 'fabi', 'class', 'top', 'generation', 'x')
            self._insert_data(r, 'tester1', start_ts, samples_count, 5)
            self._insert_data(r, 'tester2', start_ts, samples_count, 15)
            self._insert_data(r, 'tester3', start_ts, samples_count, 25)


            expected_result = [[start_ts+i, str(5)] for i in range(samples_count)]
            actual_result = r.execute_command('TS.mrange', start_ts, start_ts + samples_count, 'FILTER', 'name=bob')
            assert [['tester1', [['name', 'bob'], ['class', 'middle'], ['generation', 'x']], expected_result]] == actual_result

            def build_expected(val, time_bucket):
                return [[long(i - i%time_bucket), str(val)] for i in range(start_ts, start_ts+samples_count+1, time_bucket)]
            actual_result = r.execute_command('TS.mrange', start_ts, start_ts + samples_count, 'AGGREGATION', 'LAST', 5, 'FILTER', 'generation=x')
            expected_result = [['tester1', [['name', 'bob'], ['class', 'middle'], ['generation', 'x']], build_expected(5, 5)],
                    ['tester2', [['name', 'rudy'], ['class', 'junior'], ['generation', 'x']], build_expected(15, 5)],
                    ['tester3', [['name', 'fabi'], ['class', 'top'], ['generation', 'x']], build_expected(25, 5)],
                    ]

            assert expected_result == actual_result
            assert expected_result[1:] == r.execute_command('TS.mrange', start_ts, start_ts + samples_count,
                                                            'AGGREGATION', 'LAST', 5, 'FILTER', 'generation=x', 'class!=middle')

    def test_label_index(self):
        with self.redis() as r:
            assert r.execute_command('TS.CREATE', 'tester1', 'LABELS', 'name', 'bob', 'class', 'middle', 'generation', 'x')
            assert r.execute_command('TS.CREATE', 'tester2', 'LABELS', 'name', 'rudy', 'class', 'junior', 'generation', 'x')
            assert r.execute_command('TS.CREATE', 'tester3', 'LABELS', 'name', 'fabi', 'class', 'top', 'generation', 'x', 'x', '2')
            assert r.execute_command('TS.CREATE', 'tester4', 'LABELS', 'name', 'anybody', 'class', 'top', 'type', 'noone', 'x', '2', 'z', '3')

            assert ['tester1', 'tester2', 'tester3'] == r.execute_command('TS.QUERYINDEX', 'generation=x')
            assert ['tester1', 'tester2'] == r.execute_command('TS.QUERYINDEX', 'generation=x', 'x=')
            assert ['tester3'] == r.execute_command('TS.QUERYINDEX', 'generation=x', 'x=2')
            assert ['tester3', 'tester4'] == r.execute_command('TS.QUERYINDEX', 'x=2')
            assert ['tester1', 'tester2'] == r.execute_command('TS.QUERYINDEX', 'generation=x', 'class!=top')
            assert ['tester2'] == r.execute_command('TS.QUERYINDEX', 'generation=x', 'class!=middle', 'x=')
            assert [] == r.execute_command('TS.QUERYINDEX', 'generation=x', 'class=top', 'x=')
            assert ['tester3'] == r.execute_command('TS.QUERYINDEX', 'generation=x', 'class=top', 'z=')
            with pytest.raises(redis.ResponseError):
                r.execute_command('TS.QUERYINDEX', 'z=', 'x!=2')
            assert ['tester3'] == r.execute_command('TS.QUERYINDEX', 'z=', 'x=2')

    def test_series_ordering(self):
        with self.redis() as r:
            sample_len = 1024
            chunk_size = 4

            r.execute_command("ts.create", 'test_key', 0, chunk_size)
            for i in range(sample_len):
                r.execute_command("ts.add", 'test_key', i , i)

            res = r.execute_command('ts.range', 'test_key', 0, sample_len)
            i = 0
            for sample in res:
                assert sample == [i, str(i)]
                i += 1