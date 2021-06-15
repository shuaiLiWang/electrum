# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The starcoin Core Contributors

"""Utilities for data type converting, construction and hashing."""

import hashlib
import typing

from nacl.public import PublicKey

from electrum_gui.common.provider.chains.stc.sdk import serde_types, starcoin_types

SUB_ADDRESS_LEN: int = 8
STARCOIN_HASH_PREFIX: bytes = b"STARCOIN::"
CORE_CODE_ADDRESS: str = "0x00000000000000000000000000000001"
ACCOUNT_ADDRESS_LEN: int = 16
RESOURCE_TAG: int = 1


class InvalidAccountAddressError(Exception):
    pass


class InvalidSubAddressError(Exception):
    pass


def hex_to_tuple(input: str) -> tuple:
    if input.startswith("0x"):
        input = input[2:]
    return tuple(serde_types.uint8(x) for x in bytes.fromhex(input))


def account_address(addr: typing.Union[starcoin_types.AccountAddress, bytes, str]) -> starcoin_types.AccountAddress:
    """convert an account address from hex-encoded or bytes into `starcoin_types.AccountAddress`

    Returns given address if it is `starcoin_types.AccountAddress` already
    """

    if isinstance(addr, starcoin_types.AccountAddress):
        return addr

    try:
        if isinstance(addr, str):
            return starcoin_types.AccountAddress(hex_to_tuple(addr))
        return starcoin_types.AccountAddress(tuple(serde_types.uint8(x) for x in addr))
    except ValueError as e:
        raise InvalidAccountAddressError(e)


def account_address_hex(addr: typing.Union[starcoin_types.AccountAddress, str]) -> str:
    """convert `starcoin_types.AccountAddress` into hex-encoded string

    This function converts given parameter into account address bytes first, then convert bytes
    into hex-encoded string
    """

    return account_address_bytes(addr).hex()


def account_address_bytes(addr: typing.Union[starcoin_types.AccountAddress, str]) -> bytes:
    """convert `starcoin_types.AccountAddress` or hex-encoded account address into bytes"""

    if isinstance(addr, str):
        return account_address_bytes(account_address(addr))

    return bytes(int(i) for i in addr.value)


def sub_address(addr: typing.Union[str, bytes]) -> bytes:
    """convert hex-encoded sub-address into bytes

    This function validates bytes length, and raises `InvalidSubAddressError` if length
    does not match sub-address length (8 bytes)
    """

    ret = bytes.fromhex(addr) if isinstance(addr, str) else addr
    if len(ret) != SUB_ADDRESS_LEN:
        raise InvalidSubAddressError(
            f"{addr}(len={len(ret)}) is a valid sub-address, sub-address is {SUB_ADDRESS_LEN} bytes"
        )
    return ret


def public_key_bytes(public_key: PublicKey) -> bytes:
    """convert nacl.PublicKey into bytes"""

    return public_key.encode()


def currency_code(code: str) -> starcoin_types.TypeTag:
    """converts currency code string to starcoin_types.TypeTag"""
    if isinstance(code, str):
        return starcoin_types.TypeTag__Struct(
            value=starcoin_types.StructTag(
                address=account_address(CORE_CODE_ADDRESS),
                module=starcoin_types.Identifier(code),
                name=starcoin_types.Identifier(code),
                type_params=[],
            )
        )

    raise TypeError(f"unknown currency code type: {code}")


def create_signed_transaction(
    txn: starcoin_types.RawTransaction, public_key: bytes, signature: bytes
) -> starcoin_types.SignedUserTransaction:
    """create single signed `starcoin_types.SignedTransaction`"""
    return starcoin_types.SignedUserTransaction(
        raw_txn=txn,
        authenticator=starcoin_types.TransactionAuthenticator__Ed25519(
            public_key=starcoin_types.Ed25519PublicKey(value=public_key),
            signature=starcoin_types.Ed25519Signature(value=signature),
        ),
    )


def raw_transaction_signing_msg(txn: starcoin_types.RawTransaction) -> bytes:
    """create signing message from given `starcoin_types.RawTransaction`"""
    return starcoin_hash_seed(b"RawUserTransaction") + txn.bcs_serialize()


def transaction_hash(txn: starcoin_types.SignedUserTransaction) -> str:
    """create transaction hash from given `starcoin_types.SignedTransaction`"""

    user_txn = starcoin_types.Transaction__UserTransaction(value=txn)
    return hash(starcoin_hash_seed(b"Transaction"), user_txn.bcs_serialize()).hex()


def starcoin_hash_seed(typ: bytes) -> bytes:
    return hash(STARCOIN_HASH_PREFIX, typ)


def hash(b1: bytes, b2: bytes) -> bytes:
    hash = hashlib.sha3_256()
    hash.update(b1)
    hash.update(b2)
    return hash.digest()


def payload_bcs_decode(payload: str) -> typing.Union[starcoin_types.Script, starcoin_types.Package]:
    payload = starcoin_types.TransactionPayload.bcs_deserialize(bytes.fromhex(payload[2:])).value
    return payload