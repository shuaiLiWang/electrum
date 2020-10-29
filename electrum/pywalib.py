#!/usr/bin/env python
# -*- coding: utf-8 -*-
import http
import math
import os
import json
from enum import Enum
from os.path import expanduser

import requests
# from eth_accounts.account_utils import AccountUtils
from eth_keyfile import keyfile
from eth_utils import to_checksum_address
from web3 import HTTPProvider, Web3
from .eth_transaction import Eth_Transaction
from decimal import Decimal

ETHERSCAN_API_KEY = "R796P9T31MEA24P8FNDZBCA88UHW8YCNVW"
INFURA_PROJECT_ID = "f001ce716b6e4a33a557f74df6fe8eff"
ROUND_DIGITS = 3
DEFAULT_GAS_PRICE_GWEI = 4
DEFAULT_GAS_LIMIT = 25000
DEFAULT_GAS_SPEED = 1
KEYSTORE_DIR_PREFIX = expanduser("~")
# default pyethapp keystore path
KEYSTORE_DIR_SUFFIX = ".electrum/eth/keystore/"

REQUESTS_HEADERS = {
    "User-Agent": "https://github.com/AndreMiras/PyWallet",
}

class InsufficientFundsException(Exception):
    """
    Raised when user want to send funds and have insufficient balance on address
    """
    pass


class InsufficientERC20FundsException(Exception):
    """
    Raised when user want to send ERC20 contract tokens and have insufficient balance
    of these tokens on wallet's address
    """
    pass


class ERC20NotExistsException(Exception):
    """
    Raised when user want manipulate with token which doesn't exist in wallet.
    """
    pass


class InvalidTransactionNonceException(Exception):
    """
    Raised when duplicated nonce occur or any other problem with nonce
    """
    pass


class InvalidValueException(Exception):
    """
    Raised when some of expected values is not correct.
    """
    pass

class InvalidAddress(ValueError):
    """
    The supplied address does not have a valid checksum, as defined in EIP-55
    """
    pass

class InvalidPasswordException(Exception):
    """
    Raised when invalid password was entered.
    """
    pass


class InfuraErrorException(Exception):
    """
    Raised when wallet cannot connect to infura node.
    """

class UnknownEtherscanException(Exception):
    pass


class NoTransactionFoundException(UnknownEtherscanException):
    pass


def get_abi_json():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    abi_path = os.path.join(root_dir, '.', 'abi.json')
    with open(abi_path) as f:
        fitcoin = json.load(f)
    return fitcoin

class ChainID(Enum):
    MAINNET = 1
    MORDEN = 2
    ROPSTEN = 3
    CUSTOMER = 11


class HTTPProviderFactory:

    PROVIDER_URLS = {
        # ChainID.MAINNET: 'https://api.myetherapi.com/eth',
        ChainID.MAINNET: f"https://mainnet.infura.io/v3/{INFURA_PROJECT_ID}",
        # ChainID.ROPSTEN: 'https://api.myetherapi.com/rop',
        ChainID.ROPSTEN: f"https://ropsten.infura.io/v3/{INFURA_PROJECT_ID}",
        ChainID.CUSTOMER: f"http://127.0.0.1:8545",
    }

    @classmethod
    def create(cls, chain_id=ChainID.MAINNET) -> HTTPProvider:
        url = cls.PROVIDER_URLS[chain_id]
        return HTTPProvider(url)


def get_etherscan_prefix(chain_id=ChainID.MAINNET) -> str:
    PREFIXES = {
        ChainID.MAINNET: 'https://api.etherscan.io/api',
        ChainID.ROPSTEN: 'https://api-ropsten.etherscan.io/api',
    }
    return PREFIXES[chain_id]


def handle_etherscan_response_json(response_json):
    """Raises an exception on unexpected response json."""
    status = response_json["status"]
    message = response_json["message"]
    if status != "1":
        if message == "No transactions found":
            raise NoTransactionFoundException()
        else:
            raise UnknownEtherscanException(response_json)
    assert message == "OK"


def handle_etherscan_response_status(status_code):
    """Raises an exception on unexpected response status."""
    if status_code != http.HTTPStatus.OK:
        raise UnknownEtherscanException(status_code)


def handle_etherscan_response(response):
    """Raises an exception on unexpected response."""
    handle_etherscan_response_status(response.status_code)
    handle_etherscan_response_json(response.json())


def requests_get(url):
    return requests.get(url, headers=REQUESTS_HEADERS)

headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.83 Safari/537.36"
}

class PyWalib:
    web3 = None
    def __init__(self, chain_id=ChainID.MAINNET):
        self.chain_id = chain_id
        self.provider = HTTPProviderFactory.create(self.chain_id)
        PyWalib.web3 = Web3(self.provider)
        # #self.web3 = Web3(HTTPProvider("https://ropsten.infura.io/v3/57caa86e6f454063b13d717be8cc3408"))
        # self.web3 =f Web3(HTTPProvider("https://ropsten.infura.io/v3/f001ce716b6e4a33a557f74df6fe8eff"))
        # #self.web3 = Web3(HTTPProvider("https://mainnet.infura.io/v3/57caa86e6f454063b13d717be8cc3408"))

    @staticmethod
    def get_web3():
        return PyWalib.web3

    def get_gas_price(self):
        try:
            response = requests.get('https://www.gasnow.org/api/v3/gas/price?utm_source=onekey', headers=headers)
            obj = response.json()
            out = dict{}
            if obj['code'] == 200:
                for type, wei in obj['data'].items():
                    fee_info = dict{}
                    fee_info['price'] = self.web3.fromWei(wei, "ether")
                    if type == "rapid":
                        fee_info['time'] = "15 Seconds"
                    elif type == "fast":
                        fee_info['time'] = "1 Minute"
                    elif type == "standard":
                        fee_info['time'] = "3 Minutes"
                    elif type == "timestamp":
                        fee_info['time'] = "> 10 Minutes"
                    out[type] = fee_info
            return json.dumps(out)
        except BaseException as ex:
            raise ex

    # def get_default_price(self):
    #     return self.web3.eth.gasPrice * DEFAULT_GAS_SPEED

    # def get_fee(self, speed=1, gas_limit=DEFAULT_GAS_LIMIT, gas_price=None):
    #     print("TODO....TIME SUPPORT")
    #     print("TODO....FAIT SUPPORT")
    #     if gas_price is not None:
    #         transaction_const_wei = gas_limit * speed * gas_price
    #     else:
    #         transaction_const_wei = gas_limit * speed * self.web3.eth.gasPrice
    #     return self.web3.fromWei(transaction_const_wei, 'ether')
    #
    # def get_show_fee(self):
    #     out = dict()
    #     out['standard'] = self.get_fee(speed=1.5)
    #     out['low'] = self.get_fee(speed=1)
    #     out['fast'] = self.get_fee(speed=2)

    def send_transaction(self, account, from_address, to_address, value, contract=None, gasprice = DEFAULT_GAS_PRICE_GWEI * (10 ** 9)):
    #def send_transaction(self, account, from_address, to_address, value, contract=None, gasprice, gas_price_speed=20):
        transaction = Eth_Transaction(
            account=account,
            w3=self.web3
        )

        # check if value to send is possible to convert to the number
        try:
            float(value)
        except ValueError:
            raise InvalidValueException()

        if contract is None:  # create ETH transaction dictionary
            tx_dict = transaction.build_transaction(
                to_address=self.web3.toChecksumAddress(to_address),
                value=self.web3.toWei(value, "ether"),
                gas=25000,  # fixed gasLimit to transfer ether from one EOA to another EOA (doesn't include contracts)
                #gas_price=self.web3.eth.gasPrice * gas_price_speed,
                gas_price = gasprice,
                # be careful about sending more transactions in row, nonce will be duplicated
                nonce=self.web3.eth.getTransactionCount(self.web3.toChecksumAddress(from_address)),
                chain_id="0x539"
            )
        else:  # create ERC20 contract transaction dictionary
            erc20_decimals = contract.get_decimals()
            token_amount = int(float(value) * (10 ** erc20_decimals))
            data_for_contract = Eth_Transaction.get_tx_erc20_data_field(to_address, token_amount)

            # check whether there is sufficient ERC20 token balance
            _, erc20_balance = self.get_balance(self.web3.toChecksumAddress(from_address), contract)
            if float(value) > erc20_balance:
                raise InsufficientERC20FundsException()

            #calculate how much gas I need, unused gas is returned to the wallet
            estimated_gas = self.pywalib.web3.eth.estimateGas(
                {'to': contract.get_address(),
                 'from': from_address,
                 'data': data_for_contract
                 })

            tx_dict = transaction.build_transaction(
                to_address=contract.get_address,  # receiver address is defined in data field for this contract
                value=0,  # amount of tokens to send is defined in data field for contract
                gas=estimated_gas,
                gas_price=gasprice,
                # be careful about sending more transactions in row, nonce will be duplicated
                nonce=self.web3.eth.getTransactionCount(self.web3.toChecksumAddress(from_address)),
                chain_id="0x539",
                data=data_for_contract
            )

        # check whether to address is valid checksum address
        if not self.web3.isChecksumAddress(self.web3.toChecksumAddress(to_address)):
            raise InvalidAddress()

        # check whether there is sufficient eth balance for this transaction
        #_, balance = self.get_balance(from_address)
        balance = self.web3.fromWei(self.web3.eth.getBalance(self.web3.toChecksumAddress(from_address)), 'ether')
        transaction_const_wei = tx_dict['gas'] * tx_dict['gasPrice']
        transaction_const_eth = self.web3.fromWei(transaction_const_wei, 'ether')
        if contract is None:
            if (transaction_const_eth + Decimal(value)) > balance:
                raise InsufficientFundsException()
        else:
            if transaction_const_eth > balance:
                raise InsufficientFundsException()

        # send transaction
        tx_hash = transaction.send_transaction(tx_dict)

        print('Pending', end='', flush=True)
        while True:
            tx_receipt = self.web3.eth.getTransactionReceipt(tx_hash)
            if tx_receipt is None:
                print('.', end='', flush=True)
                import time
                time.sleep(1)
            else:
                print('\nTransaction mined!')
                break

        return tx_hash, transaction_const_eth

    # @staticmethod
    # def get_balance(address, chain_id=ChainID.MAINNET):
    #     """
    #     Retrieves the balance from etherscan.io.
    #     The balance is returned in ETH rounded to the second decimal.
    #     """
    #     address = to_checksum_address(address)
    #     url = get_etherscan_prefix(chain_id)
    #     url += (
    #         '?module=account&action=balance'
    #         '&tag=latest'
    #         f'&address={address}'
    #         f'&apikey={ETHERSCAN_API_KEY}'
    #     )
    #     response = requests_get(url)
    #     handle_etherscan_response(response)
    #     response_json = response.json()
    #     balance_wei = int(response_json["result"])
    #     balance_eth = balance_wei / float(pow(10, 18))
    #     balance_eth = round(balance_eth, ROUND_DIGITS)
    #     return balance_eth

    @staticmethod
    def get_balance(wallet_address, contract=None):
        if contract is None:
            eth_balance = PyWalib.get_web3().fromWei(PyWalib.get_web3().eth.getBalance(wallet_address), 'ether')
            return "eth", eth_balance
        else:
            erc_balance = PyWalib.get_web3().fromWei(contract.get_balance(wallet_address))
            return contract.get_symbol(), erc_balance

    # def get_balance_web3(self, address):
    #     """
    #     The balance is returned in ETH rounded to the second decimal.
    #     """
    #     address = to_checksum_address(address)
    #     balance_wei = self.web3.eth.getBalance(address)
    #     balance_eth = balance_wei / float(pow(10, 18))
    #     balance_eth = round(balance_eth, ROUND_DIGITS)
    #     return balance_eth

    @staticmethod
    def get_transaction_history(address, chain_id=ChainID.MAINNET):
        """
        Retrieves the transaction history from etherscan.io.
        """
        address = to_checksum_address(address)
        url = get_etherscan_prefix(chain_id)
        url += (
            '?module=account&action=txlist'
            '&sort=asc'
            f'&address={address}'
            f'&apikey={ETHERSCAN_API_KEY}'
        )
        response = requests_get(url)
        handle_etherscan_response(response)
        response_json = response.json()
        transactions = response_json['result']
        for transaction in transactions:
            value_wei = int(transaction['value'])
            value_eth = value_wei / float(pow(10, 18))
            value_eth = round(value_eth, ROUND_DIGITS)
            from_address = to_checksum_address(transaction['from'])
            to_address = transaction['to']
            # on contract creation, "to" is replaced by the "contractAddress"
            if not to_address:
                to_address = transaction['contractAddress']
            to_address = to_checksum_address(to_address)
            sent = from_address == address
            received = not sent
            extra_dict = {
                'value_eth': value_eth,
                'sent': sent,
                'received': received,
                'from_address': from_address,
                'to_address': to_address,
            }
            transaction.update({'extra_dict': extra_dict})
        # sort by timeStamp
        transactions.sort(key=lambda x: x['timeStamp'])
        return transactions

    @staticmethod
    def get_out_transaction_history(address, chain_id=ChainID.MAINNET):
        """
        Retrieves the outbound transaction history from Etherscan.
        """
        transactions = PyWalib.get_transaction_history(address, chain_id)
        out_transactions = []
        for transaction in transactions:
            if transaction['extra_dict']['sent']:
                out_transactions.append(transaction)
        return out_transactions

    # TODO: can be removed since the migration to web3
    @staticmethod
    def get_nonce(address, chain_id=ChainID.MAINNET):
        """
        Gets the nonce by counting the list of outbound transactions from
        Etherscan.
        """
        try:
            out_transactions = PyWalib.get_out_transaction_history(
                address, chain_id)
        except NoTransactionFoundException:
            out_transactions = []
        nonce = len(out_transactions)
        return nonce

    @staticmethod
    def handle_web3_exception(exception: ValueError):
        """
        Raises the appropriated typed exception on web3 ValueError exception.
        """
        error = exception.args[0]
        code = error.get("code")
        if code in [-32000, -32010]:
            raise InsufficientFundsException(error)
        else:
            raise UnknownEtherscanException(error)

    # def transact(self, account, to, value=0, data='', sender=None, gas=25000,
    #              gasprice=DEFAULT_GAS_PRICE_GWEI * (10 ** 9)):
    #     """
    #     Signs and broadcasts a transaction.
    #     Returns transaction hash.
    #     """
    #     #address = sender or self.get_main_account().address
    #     from_address_normalized = to_checksum_address(sender)
    #     to_address_normalized = to_checksum_address(to)
    #     nonce = self.web3.eth.getTransactionCount(from_address_normalized)
    #     transaction = {
    #         'chainId': self.chain_id.value,
    #         'gas': gas,
    #         'gasPrice': gasprice,
    #         'nonce': nonce,
    #         'to': to_address_normalized,
    #         'value': value,
    #     }
    #
    #     private_key = account.privkey
    #     signed_tx = self.web3.eth.account.signTransaction(
    #         transaction, private_key)
    #     try:
    #         tx_hash = self.web3.eth.sendRawTransaction(
    #             signed_tx.rawTransaction)
    #     except ValueError as e:
    #         self.handle_web3_exception(e)
    #     return tx_hash

    # @staticmethod
    # def deleted_account_dir(keystore_dir):
    #     """
    #     Given a `keystore_dir`, returns the corresponding
    #     `deleted_keystore_dir`.
    #     >>> keystore_dir = '/tmp/keystore'
    #     >>> PyWalib.deleted_account_dir(keystore_dir)
    #     u'/tmp/keystore-deleted'
    #     >>> keystore_dir = '/tmp/keystore/'
    #     >>> PyWalib.deleted_account_dir(keystore_dir)
    #     u'/tmp/keystore-deleted'
    #     """
    #     keystore_dir = keystore_dir.rstrip('/')
    #     keystore_dir_name = os.path.basename(keystore_dir)
    #     deleted_keystore_dir_name = "%s-deleted" % (keystore_dir_name)
    #     deleted_keystore_dir = os.path.join(
    #         os.path.dirname(keystore_dir),
    #         deleted_keystore_dir_name)
    #     return deleted_keystore_dir

    # @staticmethod
    # def _get_pbkdf2_iterations(security_ratio=None):
    #     """
    #     Returns the work-factor/iterations based on the security_ratio.
    #     """
    #     iterations = None
    #     min_security_ratio = 1
    #     max_security_ratio = 100
    #     if security_ratio is not None:
    #         if not min_security_ratio <= security_ratio <= max_security_ratio:
    #             raise ValueError(
    #                 f'security_ratio must be within {min_security_ratio} and '
    #                 f'{max_security_ratio}')
    #         kdf = 'pbkdf2'
    #         default_iterations = keyfile.get_default_work_factor_for_kdf(kdf)
    #         iterations = (default_iterations * security_ratio) / 100.0
    #         iterations = math.ceil(iterations)
    #     return iterations

    # # TODO: update docstring
    # def new_account(self, password, security_ratio=None):
    #     """
    #     Creates an account on the disk and returns it.
    #     security_ratio is a ratio of the default PBKDF2 iterations.
    #     Ranging from 1 to 100 means 100% of the iterations.
    #     """
    #     iterations = self._get_pbkdf2_iterations(security_ratio)
    #     account = self.account_utils.new_account(
    #         password=password, iterations=iterations)
    #     return account

    #def add_account(self, account):
    #    self.account_utils.add_account(account)

    # def delete_account(self, account):
    #     """
    #     Deletes the given `account` from the `keystore_dir` directory.
    #     In fact, moves it to another location; another directory at the same
    #     level.
    #     """
    #     self.account_utils.delete_account(account)
    #
    # def update_account_password(
    #         self, account, new_password, current_password=None):
    #     """
    #     The current_password is optional if the account is already unlocked.
    #     """
    #     self.account_utils.update_account_password(
    #         account, new_password, current_password)

    # @staticmethod
    # def get_default_keystore_path():
    #     """
    #     Returns the keystore path, which is the same as the default pyethapp
    #     one.
    #     """
    #     keystore_dir = os.path.join(KEYSTORE_DIR_PREFIX, KEYSTORE_DIR_SUFFIX)
    #     return keystore_dir

    # def get_account_list(self):
    #     """
    #     Returns the Account list.
    #     """
    #     accounts = self.account_utils.get_account_list()
    #     return accounts
    #
    # def get_main_account(self):
    #     """
    #     Returns the main Account.
    #     """
    #     account = self.get_account_list()[0]
    #     return account
