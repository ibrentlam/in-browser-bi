
This dashboard will be "a day in the life of bitcoin"

The parquet dataset is at:
s3://aws-public-blockchain/v1.0/btc/transactions/date=2026-04-01/part-00000-435bf6c4-b054-4f2c-905a-671d4a9b6c0a-c000.snappy.parquet 

There are 683419 ledger entries. 

We can generate graphs of block_number, size, input_value, fee, 
the ordinate could be block_timestamp by minute, quarter hour, or hour. 
etc. 

do a distribution of "fee" bucketed by say 100 ranges

do a distribution of "input_value" bucketed by say 100 ranges

We can provide slicers or toggles for: version, is_coinbase, 

