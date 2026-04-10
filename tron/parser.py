import hashlib
from decimal import Decimal

import base58

TRANSFER_METHOD_ID = 'a9059cbb'


def hex_to_tron_address(hex_addr: str) -> str:
    if hex_addr.startswith('0x'):
        hex_addr = '41' + hex_addr[2:]
    if not hex_addr.startswith('41'):
        hex_addr = '41' + hex_addr.lstrip('0')
    if len(hex_addr) < 42:
        hex_addr = hex_addr[:2] + hex_addr[2:].zfill(40)
    addr_bytes = bytes.fromhex(hex_addr)
    checksum = hashlib.sha256(hashlib.sha256(addr_bytes).digest()).digest()[:4]
    return base58.b58encode(addr_bytes + checksum).decode()


def parse_usdt_transfer(transaction: dict, usdt_contract: str) -> dict | None:
    try:
        tx_hash = transaction.get('txID')
        ret = transaction.get('ret', [])
        if not ret or ret[0].get('contractRet') != 'SUCCESS':
            return None
        raw_data = transaction.get('raw_data', {})
        contracts = raw_data.get('contract', [])
        if not contracts:
            return None
        contract_info = contracts[0]
        if contract_info.get('type') != 'TriggerSmartContract':
            return None
        value = contract_info.get('parameter', {}).get('value', {})
        contract_address_hex = value.get('contract_address', '')
        if not contract_address_hex:
            return None
        contract_address = hex_to_tron_address(contract_address_hex)
        if contract_address != usdt_contract:
            return None
        data = value.get('data', '')
        if len(data) < 136:
            return None
        if data[:8] != TRANSFER_METHOD_ID:
            return None
        to_address = hex_to_tron_address('41' + data[32:72])
        amount_raw = int(data[72:136], 16)
        amount = Decimal(amount_raw) / Decimal('1000000')
        from_address = hex_to_tron_address(value.get('owner_address', ''))
        return {'from': from_address, 'to': to_address, 'amount': amount, 'tx_hash': tx_hash, 'currency': 'USDT'}
    except Exception:
        return None


def parse_trx_transfer(transaction: dict) -> dict | None:
    try:
        tx_hash = transaction.get('txID')
        ret = transaction.get('ret', [])
        if not ret or ret[0].get('contractRet') != 'SUCCESS':
            return None
        raw_data = transaction.get('raw_data', {})
        contracts = raw_data.get('contract', [])
        if not contracts:
            return None
        contract_info = contracts[0]
        if contract_info.get('type') != 'TransferContract':
            return None
        value = contract_info.get('parameter', {}).get('value', {})
        to_hex = value.get('to_address', '')
        if not to_hex:
            return None
        to_address = hex_to_tron_address(to_hex)
        amount = Decimal(value.get('amount', 0)) / Decimal('1000000')
        from_address = hex_to_tron_address(value.get('owner_address', ''))
        return {'from': from_address, 'to': to_address, 'amount': amount, 'tx_hash': tx_hash, 'currency': 'TRX'}
    except Exception:
        return None
