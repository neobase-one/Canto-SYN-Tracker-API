#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
		  Copyright Blaze 2021.
 Distributed under the Boost Software License, Version 1.0.
	(See accompanying file LICENSE_1_0.txt or copy at
		  https://www.boost.org/LICENSE_1_0.txt)
"""

from typing import Any, Callable, Dict, Literal, Tuple

import dateutil.parser

from syn.utils.data import MORALIS_APIKEY, SYN_DATA, TOKEN_DECIMALS, \
    COVALENT_APIKEY, POPULATE_CACHE
from syn.utils.price import get_historic_price_for_address, \
    get_price_for_address, get_historic_price_syn
from syn.utils.helpers import add_to_dict, get_all_keys, merge_dict
from syn.utils.wrappa.covalent import Covalent
from syn.utils.wrappa.moralis import Moralis
from syn.utils.cache import timed_cache

covalent = Covalent(COVALENT_APIKEY)

moralis = Moralis(MORALIS_APIKEY)


def _always_true(*args, **kwargs) -> Literal[True]:
    return True


def create_totals(res: Dict[str, Any],
                  chain: str) -> Tuple[Dict[str, float], float, float]:
    # Create a `total` key for each day.
    for v in res.values():
        # Total has already been set, most likely from cache.
        if v['total'].get('usd', 0) != 0:
            continue

        total_usd: float = 0

        for _v in v.values():
            total_usd += _v['usd']

        v['total'] = {'usd': total_usd}

    total: Dict[str, float] = {}
    total_usd_current: float = 0
    # Total adjusted.
    total_usd: float = 0

    # Now create a `total` including every day.
    for v in res.values():
        total_usd += v['total']['usd']

        for token, _v in v.items():
            if 'volume' in _v:
                add_to_dict(total, token, _v['volume'])

    for k, v in total.items():
        if k != 'total':
            price = get_price_for_address(chain, k)
            total_usd_current += (price * v)

    return total, total_usd, total_usd_current


@timed_cache(360, maxsize=50)
def get_chain_volume_covalent(
        address: str,
        contract_address: str,
        chain: str,
        filter: Callable[[Dict[str, Any]],
                         bool] = _always_true) -> Dict[str, Any]:
    data = covalent.transfers_v2(address,
                                 contract_address,
                                 chain,
                                 useRedis=not POPULATE_CACHE)
    res: Dict[str, Any] = {}
    _address: str = ''

    for y in data:
        for x in y['items']:
            if filter(x):
                # TODO(blaze): there is normally only 1 transfer involved,
                # but what do we do when there is more?
                for z in x['transfers']:
                    value = z['delta_quote']
                    volume = int(z['delta']) / 10**z['contract_decimals']
                    key = str(
                        dateutil.parser.parse(z['block_signed_at']).date())
                    _address = z['contract_address']

                    if value is None:
                        if z['contract_ticker_symbol'] == 'SYN':
                            value = volume * get_historic_price_syn(key)
                        else:
                            value = volume * get_historic_price_for_address(
                                chain, z['contract_address'], key)

                    if key not in res:
                        res.update({
                            key: {
                                z['contract_address']: {},
                                'total': {
                                    'usd': 0,
                                    'volume': 0,
                                }
                            }
                        })

                    add_to_dict(res[key][z['contract_address']], 'volume',
                                volume)
                    add_to_dict(res[key][z['contract_address']], 'usd', value)

    _chain = 'ethereum' if chain == 'eth' else chain

    res = merge_dict(res, get_all_keys(f'{_chain}:*:{_address}',
                                       serialize=True))
    total, total_usd, total_usd_current = create_totals(res, chain)

    return {
        'stats': {
            'volume': total,
            'usd': {
                'adjusted': total_usd,
                'current': total_usd_current,
            },
        },
        'data': res
    }


@timed_cache(360, maxsize=50)
def get_chain_volume(
        address: str,
        chain: str,
        filter: Callable[[Dict[str, str]],
                         bool] = _always_true) -> Dict[str, Any]:
    data = moralis.erc20_transfers(address, chain, useRedis=not POPULATE_CACHE)
    res: Dict[str, Any] = {}
    _address: str = ''

    for x in data:
        if filter(x) and x['address'] in TOKEN_DECIMALS[chain]:
            value = int(x['value']) / 10**TOKEN_DECIMALS[chain][x['address']]
            key = str(dateutil.parser.parse(x['block_timestamp']).date())
            _address = x['address']

            if x['address'] == SYN_DATA['ethereum' if chain ==
                                        'eth' else chain]['address'].lower():
                price = get_historic_price_syn(key)
            else:
                price = get_historic_price_for_address(chain, x['address'],
                                                       key)

            if key not in res:
                res.update({
                    key: {
                        x['address']: {
                            'volume': value,
                            'usd': price * value,
                        },
                        'total': {
                            'usd': 0,
                            'volume': 0,
                        }
                    }
                })
            elif x['address'] not in res[key]:
                res[key].update(
                    {x['address']: {
                         'volume': value,
                         'usd': price * value,
                     }})
            else:
                res[key][x['address']]['volume'] += value
                res[key][x['address']]['usd'] += value * price

    _chain = 'ethereum' if chain == 'eth' else chain

    res = merge_dict(res, get_all_keys(f'{_chain}:*:{_address}',
                                       serialize=True))
    total, total_usd, total_usd_current = create_totals(res, chain)

    return {
        'stats': {
            'volume': total,
            'usd': {
                'adjusted': total_usd,
                'current': total_usd_current,
            },
        },
        'data': res
    }


@timed_cache(360, maxsize=50)
def get_chain_metapool_volume(
            metapool: str,
            nusd: str,
            usdlp: str,
            chain: str) -> Dict[str, Any]:
    transfers_usdlp = covalent.transfers_v2(metapool, usdlp, chain)
    usdlp_is_to_mp: Dict[str, bool] = {}

    for x in transfers_usdlp:
        for y in x['items']:
            for tx in y['transfers']:
                usdlp_is_to_mp[tx['tx_hash']] = (tx['to_address'] == metapool)

    transfers_nusd = covalent.transfers_v2(metapool, nusd, chain)
    res: Dict[str, Any] = {}

    volume_total = 0

    for x in transfers_nusd:
        for y in x['items']:
            if y['tx_hash'] in usdlp_is_to_mp:
                for tx in y['transfers']:
                    is_nusd_to_mp = (tx['to_address'] == metapool)
                    if is_nusd_to_mp != usdlp_is_to_mp[tx['tx_hash']]:
                        volume = int(tx['delta']) / 10 ** tx['contract_decimals']
                        key = str(
                            dateutil.parser.parse(tx['block_signed_at']).date())

                        add_to_dict(res, key, volume)
                        volume_total += volume
                        # nUSD = 1
                        # add_to_dict(res[key][tx['contract_address']], 'usd', volume)

    # total, total_usd, total_usd_current = create_totals(res, chain)

    return {
        'stats': {
            'volume': volume_total
        },
        'data': res
    }
