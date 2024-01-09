#!/bin/bash
export CMC_API_KEY="8cc65318-ebbb-40ad-8210-0eac79cb6338"

curl -H "X-CMC_PRO_API_KEY: $CMC_API_KEY" -H "Accept: application/json" -G "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest?id=4847&convert_id=1" | jq '.data["4847"].quote["1"].price * 100000000' &> $(dirname $0)/stx-price.txt