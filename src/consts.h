/*
* Copyright 2018-2019 Redis Labs Ltd. and Contributors
*
* This file is available under the Redis Labs Source Available License Agreement
*/
#ifndef CONSTS_H
#define CONSTS_H

#include <sys/types.h>

#define TRUE 1
#define FALSE 0

#define timestamp_t u_int64_t
#define api_timestamp_t u_int64_t
#define TSDB_ERR_TIMESTAMP_TOO_OLD -1
#define TSDB_OK 0
#define TSDB_ERROR -1
#define TSDB_NOTEXISTS 2

/* TS.CREATE Defaults */
#define RETENTION_DEFAULT_SECS          0LL
#define SAMPLES_PER_CHUNK_DEFAULT_SECS  360LL

/* TS.Range Aggregation types */
typedef enum {
    TS_AGG_INVALID = -1,
    TS_AGG_NONE = 0,
    TS_AGG_MIN,
    TS_AGG_MAX,
    TS_AGG_SUM,
    TS_AGG_AVG,
    TS_AGG_COUNT,
    TS_AGG_FIRST,
    TS_AGG_LAST,
    TS_AGG_RANGE,
    TS_AGG_TYPES_MAX // 9
} TS_AGG_TYPES_T;

#define max(a,b) \
   ({ __typeof__ (a) _a = (a); \
       __typeof__ (b) _b = (b); \
     _a > _b ? _a : _b; })

#endif