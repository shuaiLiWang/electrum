from __future__ import absolute_import, division, print_function
import asyncio
import binascii
import copy
from code import InteractiveConsole
import json
import os
import time
from decimal import Decimal
from os.path import exists, join
import pkgutil
import unittest
import threading
import random
import string
from electrum.util import (
    FailedGetTx,
    UserCancel,
    NotEnoughFunds,
    NotEnoughFundsStr,
    FileAlreadyExist,
    DerivedWalletLimit,
    UnavailablePrivateKey,
    UnavailablePublicKey,
    UnavailableEthAddr,
    UnavailableBtcAddr,
    NotSupportExportSeed,
    InvalidBip39Seed,
    InvalidPassword,
    UnavaiableHdWallet,
    ReplaceWatchonlyWallet,
)
from electrum.i18n import _, set_language
from electrum.keystore import bip44_derivation, purpose48_derivation, Imported_KeyStore
from electrum.plugin import Plugins
# from electrum.plugins.trezor.clientbase import TrezorClientBase
from electrum.transaction import PartialTransaction, Transaction, TxOutput, PartialTxOutput, tx_from_any, TxInput, \
    PartialTxInput, SerializationError
from electrum import commands, daemon, keystore, simple_config, storage, util, bitcoin, paymentrequest
from electrum.util import bfh, Fiat, create_and_start_event_loop, decimal_point_to_base_unit_name, standardize_path, \
    DecimalEncoder
from electrum.util import user_dir as get_dir
from electrum import MutiBase
from .tx_db import TxDb
from electrum.i18n import _
from .create_wallet_info import CreateWalletInfo
from electrum.storage import WalletStorage
from electrum.eth_wallet import (Imported_Eth_Wallet, Standard_Eth_Wallet, Eth_Wallet)
from electrum.wallet import (Imported_Wallet, Standard_Wallet, Wallet, Deterministic_Wallet)
from electrum.bitcoin import is_address, hash_160, COIN, TYPE_ADDRESS, base_decode
from electrum.interface import ServerAddr
from electrum import mnemonic
from electrum.mnemonic import Wordlist
from mnemonic import Mnemonic
from eth_keys import keys
from hexbytes import HexBytes
from electrum.address_synchronizer import TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE
from electrum.bip32 import BIP32Node, convert_bip32_path_to_list_of_uint32 as parse_path
from electrum.network import Network, TxBroadcastError, BestEffortRequestFailed
from electrum import ecc
from electrum.pywalib import InvalidPasswordException
from electrum.pywalib import PyWalib, ERC20NotExistsException, InvalidValueException, InvalidAddress, \
    InsufficientFundsException
from electrum.keystore import Hardware_KeyStore
from eth_account import Account
from eth_utils.address import to_checksum_address
from eth_keys.utils.address import public_key_bytes_to_address
from trezorlib.customer_ui import CustomerUI
from trezorlib import (
    btc,
    exceptions,
    firmware,
    protobuf,
    messages,
    device,
)
from trezorlib.cli import trezorctl

from electrum.bip32 import get_uncompressed_key
from electrum.wallet_db import WalletDB
from enum import Enum
from .firmware_sign_nordic_dfu import parse
from electrum import constants
from electrum.constants import read_json
from .derived_info import DerivedInfo
from electrum.util import Ticker
IS_ANDROID = True
if "iOS_DATA" in os.environ:
    from .ioscallback import CallHandler
    IS_ANDROID = False

PURPOSE_POS = 1
ACCOUNT_POS = 3
BTC_BLOCK_INTERVAL_TIME = 10
DEFAULT_ADDR_TYPE = 49
ticker = None

class Status(Enum):
    net = 1
    broadcast = 2
    sign = 3
    update_wallet = 4
    update_status = 5
    update_history = 6
    update_interfaces = 7
    create_wallet_error = 8


class AndroidConsole(InteractiveConsole):
    """`interact` must be run on a background thread, because it blocks waiting for input.
    """

    def __init__(self, app, cmds):
        namespace = dict(c=cmds, context=app)
        namespace.update({name: CommandWrapper(cmds, name) for name in all_commands})
        namespace.update(help=Help())
        InteractiveConsole.__init__(self, locals=namespace)

    def interact(self):
        try:
            InteractiveConsole.interact(
                self, banner=(
                        _("WARNING!") + "\n" +
                        _("Do not enter code here that you don't understand. Executing the wrong "
                          "code could lead to your coins being irreversibly lost.") + "\n" +
                        "Type 'help' for available commands and variables."))
        except SystemExit:
            pass


class CommandWrapper:
    def __init__(self, cmds, name):
        self.cmds = cmds
        self.name = name

    def __call__(self, *args, **kwargs):
        return getattr(self.cmds, self.name)(*args, **kwargs)


class Help:
    def __repr__(self):
        return self.help()

    def __call__(self, *args):
        print(self.help(*args))

    def help(self, name_or_wrapper=None):
        if name_or_wrapper is None:
            return ("Commands:\n" +
                    "\n".join(f"  {cmd}" for name, cmd in sorted(all_commands.items())) +
                    "\nType help(<command>) for more details.\n"
                    "The following variables are also available: "
                    "c.config, c.daemon, c.network, c.wallet, context")
        else:
            if isinstance(name_or_wrapper, CommandWrapper):
                cmd = all_commands[name_or_wrapper.name]
            else:
                cmd = all_commands[name_or_wrapper]
            return f"{cmd}\n{cmd.description}"


def verify_address(address) -> bool:
    return is_address(address, net=constants.net)


def verify_xpub(xpub: str) -> bool:
    return keystore.is_bip32_key(xpub)

# Adds additional commands which aren't available over JSON RPC.
class AndroidCommands(commands.Commands):
    def __init__(self, android_id=None, config=None, user_dir=None, callback=None, chain_type="mainnet"):
        self.asyncio_loop, self._stop_loop, self._loop_thread = create_and_start_event_loop()  # TODO:close loop
        self.config = self.init_config(config=config)
        if user_dir is None:
            self.user_dir = get_dir()
        else:
            self.user_dir = user_dir
        fd = daemon.get_file_descriptor(self.config)
        if not fd:
            raise BaseException(("Daemon already running, Don't start the wallet repeatedly"))
        set_language(self.config.get('language', 'zh_CN'))

        # Initialize here rather than in start() so the DaemonModel has a chance to register
        # its callback before the daemon threads start.
        self.daemon = daemon.Daemon(self.config, fd)
        self.coins = read_json('eth_servers.json', {})
        if constants.net.NET == "Bitcoin":
            chain_type = "mainnet"
        else:
            chain_type = "testnet"
        self.pywalib = PyWalib(self.config, chain_type=chain_type, path=self._tx_list_path(name="tx_info.db"))
        self.txdb = TxDb(path=self._tx_list_path(name="tx_info.db"))
        self.pywalib.set_server(self.coins["eth"])
        self.network = self.daemon.network
        self.daemon_running = False
        self.wizard = None
        self.plugin = Plugins(self.config, 'cmdline')
        self.label_plugin = self.plugin.load_plugin("labels")
        self.label_flag = self.config.get('use_labels', False)
        self.callbackIntent = None
        self.hd_wallet = None
        self.check_pw_wallet = None
        self.wallet = None
        self.client = None
        self.recovery_wallets = {}
        self.path = ''
        self.backup_info = self.config.get("backupinfo", {})
        self.derived_info = self.config.get("derived_info", dict())
        self.replace_wallet_info = {}
        ran_str = self.config.get("ra_str", None)
        if ran_str is None:
            ran_str = ''.join(random.sample(string.ascii_letters + string.digits, 8))
            self.config.set_key("ra_str", ran_str)
        self.android_id = android_id + ran_str
        self.lock = threading.RLock()
        self.init_local_wallet_info()
        if self.network:
            interests = ['wallet_updated', 'network_updated', 'blockchain_updated',
                         'status', 'new_transaction', 'verified', 'set_server_status']
            util.register_callback(self.on_network_event, interests)
            util.register_callback(self.on_fee, ['fee'])
            # self.network.register_callback(self.on_fee_histogram, ['fee_histogram'])
            util.register_callback(self.on_quotes, ['on_quotes'])
            util.register_callback(self.on_history, ['on_history'])
        self.fiat_unit = self.daemon.fx.ccy if self.daemon.fx.is_enabled() else ''
        self.decimal_point = self.config.get('decimal_point', util.DECIMAL_POINT_DEFAULT)
        self.hw_info = {}
        for k, v in util.base_units_inverse.items():
            if k == self.decimal_point:
                self.base_unit = v
        self.old_history_len = 0
        self.old_history_info = []
        self.num_zeros = int(self.config.get('num_zeros', 0))
        self.config.set_key('log_to_file', True, save=True)
        self.rbf = self.config.get("use_rbf", True)
        self.ccy = self.daemon.fx.get_currency()
        self.pre_balance_info = ""
        self.addr_index = 0
        self.rbf_tx = ""
        self.m = 0
        self.n = 0
        # self.sync_timer = None
        self.config.set_key('auto_connect', True, True)
        global ticker
        ticker = Ticker(5.0, self.ticker_action)
        ticker.start()
        if IS_ANDROID:
            if callback is not None:
                self.set_callback_fun(callback)
        else:
            self.my_handler = CallHandler.alloc().init()
            self.set_callback_fun(self.my_handler)
        self.start_daemon()
        self.get_block_info()
    _recovery_flag = True

    def init_config(self, config=None):
        config_options = {}
        config_options['auto_connect'] = True
        if config is None:
            out_config = simple_config.SimpleConfig(config_options)
        else:
            out_config = config
        return out_config

    def set_language(self, language):
        '''
        Set the language of error messages displayed to users
        :param language: zh_CN/en_UK as string
        '''
        set_language(language)
        self.config.set_key("language", language)

    # BEGIN commands from the argparse interface.
    def stop_loop(self):
        self.asyncio_loop.call_soon_threadsafe(self._stop_loop.set_result, 1)
        self._loop_thread.join(timeout=1)

    def update_local_wallet_info(self, name, wallet_type):
        wallet_info = {}
        wallet_info['type'] = wallet_type
        wallet_info['time'] = time.time()
        # wallet_info['xpubs'] = keystores
        wallet_info['seed'] = ""
        self.local_wallet_info[name] = wallet_info
        self.config.set_key('all_wallet_type_info', self.local_wallet_info)

    def init_local_wallet_info(self):
        try:
            self.local_wallet_info = self.config.get("all_wallet_type_info", {})
            new_wallet_info = {}
            name_wallets = ([name for name in os.listdir(self._wallet_path())])
            for name, info in self.local_wallet_info.items():
                if name_wallets.__contains__(name):
                    if not info.__contains__('time'):
                        info['time'] = time.time()
                    if not info.__contains__('xpubs'):
                        info['xpubs'] = []
                    # if not info.__contains__('seed'):
                    info['seed'] = ""
                    new_wallet_info[name] = info
            self.local_wallet_info = new_wallet_info
            self.config.set_key('all_wallet_type_info', self.local_wallet_info)
        except BaseException as e:
            raise e

    def on_fee(self, event, *arg):
        try:
            self.fee_status = self.config.get_fee_status()
        except BaseException as e:
            raise e

    # def on_fee_histogram(self, *args):
    #     self.update_history()

    def on_quotes(self, d):
        if self.wallet is not None and not self.coins.__contains__(self.wallet.wallet_type[0:3]):
            self.update_status()
        # self.update_history()

    def on_history(self, d):
        if self.wallet:
            self.wallet.clear_coin_price_cache()
        # self.update_history()

    def update_status(self):
        out = {}
        status = ''
        if not self.wallet:
            return
        if self.network is None or not self.network.is_connected():
            print("network is ========offline")
            status = _("Offline")
        elif self.network.is_connected():
            self.num_blocks = self.network.get_local_height()
            server_height = self.network.get_server_height()
            server_lag = self.num_blocks - server_height
            if not self.wallet.up_to_date or server_height == 0:
                num_sent, num_answered = self.wallet.get_history_sync_state_details()
                status = ("{} [size=18dp]({}/{})[/size]"
                          .format(_("Synchronizing..."), num_answered, num_sent))
            elif server_lag > 1:
                status = _("Server is lagging ({} blocks)").format(server_lag)
            else:
                c, u, x = self.wallet.get_balance()
                text = _("Balance") + ": %s " % (self.format_amount_and_units(c))
                show_balance = c + u
                out['balance'] = self.format_amount(show_balance)
                out['fiat'] = self.daemon.fx.format_amount_and_units(show_balance) if self.daemon.fx else None
                if u:
                    out['unconfirmed'] = self.format_amount(u, is_diff=True).strip()
                    text += " [%s unconfirmed]" % (self.format_amount(u, is_diff=True).strip())
                if x:
                    out['unmatured'] = self.format_amount(x, is_diff=True).strip()
                    text += " [%s unmatured]" % (self.format_amount(x, is_diff=True).strip())
                if self.wallet.lnworker:
                    l = self.wallet.lnworker.get_balance()
                    text += u'    \U0001f5f2 %s' % (self.format_amount_and_units(l).strip())

                # append fiat balance and price
                if self.daemon.fx.is_enabled():
                    text += self.daemon.fx.get_fiat_status_text(c + u + x, self.base_unit, self.decimal_point) or ''
            #print("update_statue out = %s" % (out))
            self.callbackIntent.onCallback("update_status=%s" % json.dumps(out))

    def get_remove_flag(self, tx_hash):
        height = self.wallet.get_tx_height(tx_hash).height
        if height in [TX_HEIGHT_FUTURE, TX_HEIGHT_LOCAL]:
            return True
        else:
            return False

    def remove_local_tx(self, delete_tx):
        '''
        :param delete_tx: tx_hash that you need to delete
        :return :
        '''
        try:
            to_delete = {delete_tx}
            to_delete |= self.wallet.get_depending_transactions(delete_tx)
            for tx in to_delete:
                self.wallet.remove_transaction(tx)
                self.delete_tx(tx)
            self.wallet.save_db()
        except BaseException as e:
            raise e
        # need to update at least: history_list, utxo_list, address_list
        # self.parent.need_update.set()

    def delete_tx(self, hash):
        try:
            if self.label_flag and self.wallet.wallet_type != "standard":
                self.label_plugin.push_tx(self.wallet, 'deltx', hash)
        except BaseException as e:
            if e != 'Could not decode:':
                print(f"push_tx delete_tx error {e}")
            pass

    def get_wallet_info(self):
        wallet_info = {}
        wallet_info['balance'] = self.balance
        wallet_info['fiat_balance'] = self.fiat_balance
        wallet_info['name'] = self.wallet.get_name()
        return json.dumps(wallet_info)

    def update_interfaces(self):
        net_params = self.network.get_parameters()
        self.num_nodes = len(self.network.get_interfaces())
        self.num_chains = len(self.network.get_blockchains())
        chain = self.network.blockchain()
        self.blockchain_forkpoint = chain.get_max_forkpoint()
        self.blockchain_name = chain.get_name()
        interface = self.network.interface
        if interface:
            self.server_host = interface.host
        else:
            self.server_host = str(net_params.server.host) + ' (connecting...)'
        self.proxy_config = net_params.proxy or {}
        mode = self.proxy_config.get('mode')
        host = self.proxy_config.get('host')
        port = self.proxy_config.get('port')
        self.proxy_str = (host + ':' + port) if mode else _('None')
        # self.callbackIntent.onCallback("update_interfaces")

    def update_wallet(self):
        self.update_status()
        # self.callbackIntent.onCallback("update_wallet")

    def on_network_event(self, event, *args):
        if self.wallet is not None and not self.coins.__contains__(self.wallet.wallet_type[0:3]):
            if event == 'network_updated':
                self.update_interfaces()
                self.update_status()
            elif event == 'wallet_updated':
                self.update_status()
                self.update_wallet()
            elif event == 'blockchain_updated':
                # to update number of confirmations in history
                self.update_wallet()
            elif event == 'status':
                self.update_status()
            elif event == 'new_transaction':
                self.update_wallet()
            elif event == 'verified':
                self.update_wallet()
            elif event == 'set_server_status':
                if self.callbackIntent is not None:
                    self.callbackIntent.onCallback("set_server_status=%s" % args[0])

    def ticker_action(self):
        if self.wallet is not None and not self.coins.__contains__(self.wallet.wallet_type[0:3]):
            self.update_wallet()
            self.update_interfaces()

    def daemon_action(self):
        self.daemon_running = True
        self.daemon.run_daemon()

    def start_daemon(self):
        t1 = threading.Thread(target=self.daemon_action)
        t1.setDaemon(True)
        t1.start()
        # import time
        # time.sleep(1.0)
        print("parent thread")

    def status(self):
        """Get daemon status"""
        try:
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        return self.daemon.run_daemon({"subcommand": "status"})

    def stop(self):
        """Stop the daemon"""
        try:
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        self.daemon.stop()
        self.daemon.join()
        self.daemon_running = False

    def get_wallet_type(self, name):
        return self.local_wallet_info.get(name)

    def set_hd_wallet(self, wallet_obj):
        if self.hd_wallet is None:
            self.hd_wallet = wallet_obj

    def is_have_hd_wallet(self, wallet_type):
        return (-1 != wallet_type.find('-hd-') or -1 != wallet_type.find('-derived-')) and -1 == wallet_type.find('-hw-')

    def load_wallet(self, name, password=None):
        '''
        load an wallet
        :param name: wallet name as a string
        :param password: the wallet password as a string
        :return:
        '''
        try:
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        path = self._wallet_path(name)
        wallet = self.daemon.get_wallet(path)
        if not wallet:
            storage = WalletStorage(path)
            if not storage.file_exists():
                # (_("Your {} were successfully imported").format(title))
                raise BaseException(_("Failed to load file {}".format(path)))
            if storage.is_encrypted():
                if not password:
                    raise BaseException(util.InvalidPassword())
                storage.decrypt(password)
            db = WalletDB(storage.read(), manual_upgrades=False)
            if db.requires_split():
                return
            if db.requires_upgrade():
                return
            if db.get_action():
                return

            if self.coins.__contains__(db.data['wallet_type'][0:3]):
                wallet = Eth_Wallet(db, storage, config=self.config)
            else:
                wallet = Wallet(db, storage, config=self.config)
                wallet.start_network(self.network)
            if self.local_wallet_info.__contains__(name):
                wallet_type = self.local_wallet_info.get(name)['type']
                if self.is_have_hd_wallet(wallet_type):
                    self.set_hd_wallet(wallet)
                # if -1 != wallet_type.find('btc-hd-'):
                #     self.btc_derived_info.set_hd_wallet(wallet)
                # elif -1 != wallet_type.find('%s-hd' %wallet_type[0:3]):
                #     self.coins[wallet_type[0:3]]['derivedInfoObj'].set_hd_wallet(wallet)
            self.daemon.add_wallet(wallet)

    def close_wallet(self, name=None):
        """Close a wallet"""
        try:
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        self.daemon.stop_wallet(self._wallet_path(name))

    def set_syn_server(self, flag):
        '''
        Enable/disable sync server
        :param flag: flag as bool
        :return: raise except if error
        '''
        try:
            self.label_flag = flag
            self.config.set_key('use_labels', bool(flag))
            if self.label_flag and self.wallet and self.wallet.wallet_type != "btc-standard" and self.wallet.wallet_type != "eth-standard":
                self.label_plugin.load_wallet(self.wallet)
        except Exception as e:
            raise BaseException(e)

    # #set callback##############################
    def set_callback_fun(self, callbackIntent):
        self.callbackIntent = callbackIntent

    # craete multi wallet##############################
    def set_multi_wallet_info(self, name, m, n):
        try:
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        if self.wizard is not None:
            self.wizard = None
        self.wizard = MutiBase.MutiBase(self.config)
        path = self._wallet_path(name)
        print("console:set_multi_wallet_info:path = %s---------" % path)
        self.wizard.set_multi_wallet_info(path, m, n)
        self.m = m
        self.n = n

    def add_xpub(self, xpub, device_id=None, account_id=0, type=84, coin='btc'):
        try:
            print(f"add_xpub........{xpub, device_id}")
            self._assert_daemon_running()
            self._assert_wizard_isvalid()
            if BIP32Node.from_xkey(xpub).xtype != 'p2wsh' and self.n >= 2:
                xpub = BIP32Node.get_p2wsh_from_other(xpub)
            coinid = None
            if coin in self.coins:
                coinid = self.coins[coin]['coinId']
            self.wizard.restore_from_xpub(xpub, device_id, account_id, type=type, coin=coin, coinid=coinid)
        except Exception as e:
            raise BaseException(e)

    def get_device_info(self, wallet_info):
        '''
        Get hardware device info
        :return:
        '''
        try:
            device_info = wallet_info.get_device_info()
            return device_info
        except BaseException as e:
            raise e

    def delete_xpub(self, xpub):
        '''
        Delete xpub when create multi-signature wallet
        :param xpub: WIF pubkey
        :return:
        '''
        try:
            self._assert_daemon_running()
            self._assert_wizard_isvalid()
            self.wizard.delete_xpub(xpub)
        except Exception as e:
            raise BaseException(e)

    def get_keystores_info(self):
        try:
            self._assert_daemon_running()
            self._assert_wizard_isvalid()
            ret = self.wizard.get_keystores_info()
        except Exception as e:
            raise BaseException(e)
        return ret

    def set_sync_server_host(self, ip, port):
        '''
        Set sync server host/port
        :param ip: the server host (exp..."127.0.0.1")
        :param port: the server port (exp..."port")
        :return: raise except if error
        '''
        try:
            if self.label_flag:
                self.label_plugin.set_host(ip, port)
                self.config.set_key('sync_server_host', '%s:%s' % (ip, port))
                data = self.label_plugin.ping_host()
        except BaseException as e:
            raise e

    def get_sync_server_host(self):
        '''
        Get sync server host,you can pull label/xpubs/tx to server
        :return: ip+port like "39.105.86.163:8080"
        '''
        try:
            return self.config.get('sync_server_host', "39.105.86.163:8080")
        except BaseException as e:
            raise e

    def get_cosigner_num(self):
        try:
            self._assert_daemon_running()
            self._assert_wizard_isvalid()
        except Exception as e:
            raise BaseException(e)
        return self.wizard.get_cosigner_num()

    def create_multi_wallet(self, name, hd=False, hide_type=False, coin='btc') -> str:
        try:
            self._assert_daemon_running()
            self._assert_wizard_isvalid()
            temp_path = self.get_temp_file()
            path = self._wallet_path(temp_path)
            wallet_type = "%s-hw-%s-%s" % (coin, self.m, self.n)
            keystores = self.get_keystores_info()
            storage, db = self.wizard.create_storage(path=path, password=None, hide_type=hide_type, coin=coin)
        except Exception as e:
            raise BaseException(e)
        if storage:
            if 'btc' == coin:
                wallet = Wallet(db, storage, config=self.config)
            else:
                wallet = Eth_Wallet(db, storage, config=self.config)
            self.check_exist_file(wallet)
            wallet.status_flag = ("btc-hw-%s-%s" % (self.m, self.n))
            wallet.hide_type = hide_type
            wallet.set_name(name)
            wallet.save_db()
            self.daemon.add_wallet(wallet)
            wallet.update_password(old_pw=None, new_pw=None, str_pw=self.android_id, encrypt_storage=True)
            self.update_wallet_name(temp_path, self.get_unique_path(wallet))
            wallet = self.get_wallet_by_name(self.get_unique_path(wallet))
            if 'btc' == coin:
                wallet.start_network(self.daemon.network)
            # if hd:
            #     wallet_type = "btc-hd-hw-%s-%s" % (self.m, self.n)
            # else:
            wallet_type = "%s-hw-derived-%s-%s" % (coin, self.m, self.n)

            if not hide_type:
                self.update_local_wallet_info(self.get_unique_path(wallet), wallet_type)
            self.wallet = wallet
            self.wallet_name = wallet.basename()
            print("console:create_multi_wallet:wallet_name = %s---------" % self.wallet_name)
            # self.select_wallet(self.wallet_name)
            if self.label_flag and not hide_type:
                wallet_name = ""
                if wallet_type[0:1] == '1':
                    wallet_name = name
                else:
                    wallet_name = "共管钱包"
                self.label_plugin.create_wallet(self.wallet, wallet_type, wallet_name)
        self.wizard = None
        wallet_info = CreateWalletInfo.create_wallet_info(coin_type="btc", name=self.wallet_name)
        out = self.get_create_info_by_json(wallet_info=wallet_info)
        return json.dumps(out)

    def pull_tx_infos(self):
        '''
        Get real-time multi-signature transaction info from sync_server
        '''
        try:
            self._assert_wallet_isvalid()
            if self.label_flag and self.wallet.wallet_type != "standard":
                data = self.label_plugin.pull_tx(self.wallet)
                data_list = json.loads(data)
                except_list = []
                data_list.reverse()
                for txinfo in data_list:
                    try:
                        tx = tx_from_any(txinfo['tx'])
                        tx.deserialize()
                        self.do_save(tx)
                        # print(f"pull_tx_infos============ save ok {txinfo['tx_hash']}")
                    except BaseException as e:
                        # print(f"except-------- {txinfo['tx_hash'],txinfo['tx']}")
                        temp_data = {}
                        temp_data['tx_hash'] = txinfo['tx_hash']
                        temp_data['error'] = str(e)
                        except_list.append(temp_data)
                        pass
                # return json.dumps(except_list)
            # self.sync_timer = threading.Timer(5.0, self.pull_tx_infos)
            # self.sync_timer.start()
        except BaseException as e:
            raise BaseException(e)

    def bulk_create_wallet(self, wallets_info):
        '''
        Create wallets in bulk
        :param wallets_info:[{m,n,name,[xpub1,xpub2,xpub3]}, ....]
        :return:
        '''
        wallets_list = json.loads(wallets_info)
        create_failed_into = {}
        for m, n, name, xpubs in wallets_list:
            try:
                self.import_create_hw_wallet(name, m, n, xpubs)
            except BaseException as e:
                create_failed_into[name] = str(e)
        return json.dumps(create_failed_into)

    def import_create_hw_wallet(self, name, m, n, xpubs, hide_type=False, hd=False, path='bluetooth', coin='btc'):
        '''
        Create a wallet
        :param name: wallet name as string
        :param m: number of consigner as string
        :param n: number of signers as string
        :param xpubs: all xpubs as [[xpub1, device_id], [xpub2, device_id],....]
        :param hide_type: whether to create a hidden wallet as bool
        :param hd: whether to create hd wallet as bool
        :param derived: whether to create hd derived wallet as bool
        :param coin: btc/eth/bsc as string
        :return: json like {'seed':''
                            'wallet_info':''
                            'derived_info':''}
        '''
        try:
            if hd:
                return self.recovery_hd_derived_wallet(xpub=self.hw_info['xpub'], hw=True, path=path)
            print(f"xpubs=========={m, n, xpubs}")
            self.set_multi_wallet_info(name, m, n)
            xpubs_list = json.loads(xpubs)
            for xpub_info in xpubs_list:
                if len(xpub_info) == 2:
                    self.add_xpub(xpub_info[0], xpub_info[1], account_id=self.hw_info['account_id'],
                                  type=self.hw_info['type'], coin=coin)
                else:
                    self.add_xpub(xpub_info, account_id=self.hw_info['account_id'], type=self.hw_info['type'], coin=coin)
            wallet_name = self.create_multi_wallet(name, hd=hd, hide_type=hide_type, coin=coin)
            if len(self.hw_info) != 0:
                bip39_path = self.get_coin_derived_path(self.hw_info['account_id'], coin=coin)
                self.update_devired_wallet_info(bip39_path, self.hw_info['xpub'], name)
            return wallet_name
        except BaseException as e:
            raise e


    ############
    def get_wallet_info_from_server(self, xpub):
        '''
        Get all wallet info that created by the xpub
        :param xpub: xpub from read by hardware as str
        :return:
        '''
        try:
            if self.label_flag:
                Vpub_data = []
                title = ('Vpub' if constants.net.TESTNET else 'Zpub')
                if xpub[0:4] == title:
                    Vpub_data = json.loads(self.label_plugin.pull_xpub(xpub))
                    xpub = BIP32Node.get_p2wpkh_from_p2wsh(xpub)
                vpub_data = json.loads(self.label_plugin.pull_xpub(xpub))
                return json.dumps(Vpub_data + vpub_data if Vpub_data is not None else vpub_data)
        except BaseException as e:
            raise e

    # #create tx#########################
    def get_default_fee_status(self):
        '''
        Get default fee,now is ETA, for btc only
        :return: The fee info as 180sat/byte when you set regtest net and the mainnet is obtained in real time
        '''
        try:
            x = 1
            self.config.set_key('mempool_fees', x == 2)
            self.config.set_key('dynamic_fees', x > 0)
            return self.config.get_fee_status()
        except BaseException as e:
            raise e

    def get_amount(self, amount):
        try:
            x = Decimal(str(amount))
        except:
            return None
        # scale it to max allowed precision, make it an int
        power = pow(10, self.decimal_point)
        max_prec_amount = int(power * x)
        return max_prec_amount

    def set_dust(self, dust_flag):
        '''
        Enable/disable use dust
        :param dust_flag: as bool
        :return:
        '''
        if dust_flag != self.config.get('dust_flag', True):
            self.dust_flag = dust_flag
            self.config.set_key('dust_flag', self.dust_flag)

    def parse_output(self, outputs):
        all_output_add = json.loads(outputs)
        outputs_addrs = []
        for key in all_output_add:
            for address, amount in key.items():
                if amount != "!":
                    amount = self.get_amount(amount)
                    if amount <= 546:
                        raise BaseException(_("Dust transaction"))
                outputs_addrs.append(PartialTxOutput.from_address_and_value(address, amount))
        return outputs_addrs

    def get_coins(self, coins_info):
        coins = []
        for utxo in self.coins:
            info = utxo.to_json()
            temp_utxo = {}
            temp_utxo[info['prevout_hash']] = info['address']
            if coins_info.__contains__(temp_utxo):
                coins.append(utxo)
        return coins

    def get_best_block_by_feerate(self, feerate, list):
        if feerate < list[20]:
            return 25
        elif list[20] <= feerate < list[10]:
            return 20
        elif list[10] <= feerate < list[5]:
            return 10
        elif list[5] <= feerate < list[4]:
            return 5
        elif list[4] <= feerate < list[3]:
            return 4
        elif list[3] <= feerate < list[2]:
            return 3
        elif list[2] <= feerate < list[1]:
            return 2
        elif list[1] <= feerate:
            return 1

    def format_return_data(self, feerate, size, block):
        fee = float(feerate / 1000) * size
        ret_data = {
            'fee': self.format_amount(fee),
            'feerate': feerate / 1000,
            'time': block * BTC_BLOCK_INTERVAL_TIME,
            'fiat': self.daemon.fx.format_amount_and_units(fee) if self.daemon.fx else None,
            'size': size
        }
        return ret_data

    def get_default_fee_info(self, feerate=None, coin="btc", eth_tx_info=None):
        '''
        Get default fee info for btc
        :param feerate: Custom rates need to be sapcified as true
        :param coin: btc or eth, btc default
        :param eth_tx_info: optional, dict contains one of: to_address, contract_address, value, data
        :return:
        if coin is "btc":
            if feerate is true:
                return data like {"customer":{"fee":"","feerate":, "time":"", "fiat":"", "size":""}}
            if feerate is None:
                return data like {"slow":{"fee":"","feerate":, "time":"", "fiat":"", "size":""},
                                  "normal":{"fee":"","feerate":, "time":"", "fiat":"", "size":""},
                                  "fast":{"fee":"","feerate":, "time":"", "fiat":"", "size":""},
                                  "slowest":{"fee":"","feerate":, "time":"", "fiat":"", "size":""}}
        else:
            return data like
            {"rapid": {"gas_price": 87, "time": 0.25, "gas_limit": 40000, "fee": "0.00348", "fiat": "4.77 USD"},
            "fast": {"gas_price": 86, "time": 1, "gas_limit": 40000, "fee": "0.00344", "fiat": "4.71 USD"},
            "normal": {"gas_price": 79, "time": 3, "gas_limit": 40000, "fee": "0.00316", "fiat": "4.33 USD"},
            "slow": {"gas_price": 72, "time": 10, "gas_limit": 40000, "fee": "0.00288", "fiat": "3.95 USD"}}
        '''
        self._assert_wallet_isvalid()
        if coin == "eth":
            if eth_tx_info:
                eth_tx_info = json.loads(eth_tx_info)
            else:
                eth_tx_info = {}

            eth_tx_info.pop("gas_price", None)
            eth_tx_info.pop("gas_limit", None)
            fee = self.eth_estimate_fee(self.wallet.get_addresses()[0], **eth_tx_info)
            return json.dumps(fee, cls=DecimalEncoder)

        fee_info_list = self.get_block_info()
        out_size_p2pkh = 141
        out_info = {}
        if feerate is None:
            for block, feerate in fee_info_list.items():
                if block == 2 or block == 5 or block == 10:
                    key = "slow" if block == 10 else "normal" if block == 5 else "fast" if block == 2 else "slowest"
                    out_info[key] = self.format_return_data(feerate, out_size_p2pkh, block)
        else:
            block = self.get_best_block_by_feerate(float(feerate) * 1000, fee_info_list)
            out_info["customer"] = self.format_return_data(float(feerate) * 1000, out_size_p2pkh, block)
        return json.dumps(out_info)

    def eth_estimate_fee(self, from_address, to_address="", contract_address=None, value="0", data="", gas_price=None, gas_limit=None):
        estimate_gas_prices = self.pywalib.get_gas_price()
        if gas_price:
            gas_price = Decimal(gas_price)
            best_time = self.pywalib.get_best_time_by_gas_price(gas_price, estimate_gas_prices)
            estimate_gas_prices = {"customer": {"gas_price": gas_price, "time": best_time}}

        if not gas_limit:
            gas_limit = self.pywalib.estimate_gas_limit(from_address, to_address, self.wallet.get_contract_token(contract_address), value, data)

        gas_limit = Decimal(gas_limit)
        last_price = PyWalib.get_coin_price("eth") or "0"

        for val in estimate_gas_prices.values():
            val["gas_limit"] = gas_limit
            val["fee"] = self.pywalib.web3.fromWei(gas_limit * self.pywalib.web3.toWei(val["gas_price"], "gwei"), "ether")
            val["fiat"] = Decimal(val["fee"]) * Decimal(last_price)
            val["fiat"] = self.daemon.fx.format_amount_and_units(val['fiat'] * COIN) or f"0 {self.ccy}"

        return estimate_gas_prices

    def get_block_info(self):
        fee_info_list = self.config.get_block_fee_info()
        if fee_info_list is not None:
            self.config.set_key("fee_info_list", fee_info_list)
        else:
            fee_info_list = self.config.get("fee_info_list", fee_info_list)
            if fee_info_list is None:
                fee_info = read_json('server_config.json', {})
                fee_info_list = fee_info['feerate_info']
        return fee_info_list

    def get_fee_by_feerate(self, coin="btc", outputs=None, message=None, feerate=None, customer=None, eth_tx_info=None):
        '''
        Get fee info when Send
        :param coin: btc or eth, btc default
        :param outputs: Outputs info as json [{addr1, value}, ...]
        :param message: What you want say as sting
        :param feerate: Feerate retruned by get_default_fee_status api
        :param customer: User choose coin as bool
        :param eth_tx_info: optional, dict contains one of: to_address, contract_address, value, data, gas_price, gas_limit
        :return:
        if coin is "btc"
        json like {"amount": 0.5 BTC,
                            "size": 141,
                            "fee": 0.0003 BTC,
                            "time": 30,
                            "tx": ""}
        else if coin is "eth":
        json like {"gas_price": 110, "time": 0.25, "gas_limit": 36015, "fee": "0.00396165", "fiat": "5.43 USD"}
        '''
        if coin == "eth":
            if eth_tx_info:
                eth_tx_info = json.loads(eth_tx_info)
            else:
                eth_tx_info = {}
            if not eth_tx_info.get("gas_price"):
                raise InvalidValueException()
            fee = self.eth_estimate_fee(self.wallet.get_addresses()[0], **eth_tx_info)
            fee = fee.get("customer")
            return json.dumps(fee, cls=DecimalEncoder)

        try:
            self._assert_wallet_isvalid()
            outputs_addrs = self.parse_output(outputs)
            if customer is None:
                coins = self.wallet.get_spendable_coins(domain=None)
            else:
                coins = self.get_coins(json.loads(customer))
            print(f"coins=========={coins}")
            c, u, x = self.wallet.get_balance()
            if not coins and self.config.get('confirmed_only', False):
                raise BaseException(_("Please use unconfirmed utxo."))
            fee_per_kb = 1000 * Decimal(feerate)
            from functools import partial
            fee_estimator = partial(self.config.estimate_fee_for_feerate, fee_per_kb)
            # tx = self.wallet.make_unsigned_transaction(coins=coins, outputs = outputs_addrs, fee=self.get_amount(fee_estimator))
            tx = self.wallet.make_unsigned_transaction(coins=coins, outputs=outputs_addrs, fee=fee_estimator)
            tx.set_rbf(self.rbf)
            self.wallet.set_label(tx.txid(), message)
            size = tx.estimated_size()
            fee = tx.get_fee()
            print("feee-----%s size =%s" % (fee, size))
            self.tx = tx
            # print("console:mkun:tx====%s" % self.tx)
            tx_details = self.wallet.get_tx_info(tx)
            print("tx_details 1111111 = %s" % json.dumps(tx_details))

            fee_info_list = self.get_block_info()
            block = self.get_best_block_by_feerate(float(feerate) * 1000, fee_info_list)
            ret_data = {
                'amount': self.format_amount(tx_details.amount),
                'size': size,
                'fee': self.format_amount(tx_details.fee),
                'time': block * BTC_BLOCK_INTERVAL_TIME,
                'tx': str(self.tx)
            }
            return json.dumps(ret_data)
        except NotEnoughFunds:
            raise BaseException(NotEnoughFundsStr())
        except BaseException as e:
            raise BaseException(e)

    def mktx(self, tx=None):
        '''
        Confirm to create transaction, for btc only
        :param tx: tx that created by get_fee_by_feerate
        :return: json like {"tx":""}
        '''

        try:
            self._assert_wallet_isvalid()
            tx = tx_from_any(tx)
            tx.deserialize()
            try:
                print(f"do save tx")
                # self.txdb.add_tx_info(self.wallet.get_addresses()[0], tx, tx.txid())
                # self.do_save(tx)
            except BaseException as e:
                pass
        except Exception as e:
            raise BaseException(e)

        ret_data = {
            'tx': str(self.tx)
        }
        try:
            if self.label_flag and self.wallet.wallet_type != "standard":
                self.label_plugin.push_tx(self.wallet, 'createtx', tx.txid(), str(self.tx))
        except Exception as e:
            print(f"push_tx createtx error {e}")
            pass
        json_str = json.dumps(ret_data)
        return json_str

    def deserialize(self, raw_tx):
        try:
            tx = Transaction(raw_tx)
            tx.deserialize()
        except Exception as e:
            raise BaseException(e)

    def eth_max_button_use_gas(self, gas_price, coin=None):
        '''
        The gas value when the max button is clicked, for eth only
        :param gas_price: gas price by coustomer
        :param coin: eth/bsc
        :return: gas info as string, unit is ether
        '''
        from electrum.pywalib import DEFAULT_GAS_LIMIT, GWEI_BASE
        return PyWalib.get_max_use_gas(gas_price)

    # ### coinjoin
    # def join_tx_with_another(self, tx: 'PartialTransaction', other_tx: 'PartialTransaction') -> None:
    #     if tx is None or other_tx is None:
    #         raise BaseException("tx or other_tx is empty")
    #     try:
    #         print(f"join_tx_with_another.....in.....")
    #         tx = tx_from_any(tx)
    #         other_tx = tx_from_any(other_tx)
    #         if not isinstance(tx, PartialTransaction):
    #             raise BaseException('TX must partial transactions.')
    #     except BaseException as e:
    #         raise BaseException(("Bixin was unable to parse your transaction") + ":\n" + repr(e))
    #     try:
    #         print(f"join_tx_with_another.......{tx, other_tx}")
    #         tx.join_with_other_psbt(other_tx)
    #     except BaseException as e:
    #         raise BaseException(("Error joining partial transactions") + ":\n" + repr(e))
    #     return tx.serialize_as_bytes().hex()

    # def export_for_coinjoin(self, export_tx) -> PartialTransaction:
    #     if export_tx is None:
    #         raise BaseException("export_tx is empty")
    #     export_tx = tx_from_any(export_tx)
    #     if not isinstance(export_tx, PartialTransaction):
    #         raise BaseException("Can only export partial transactions for coinjoins.")
    #     tx = copy.deepcopy(export_tx)
    #     tx.prepare_for_export_for_coinjoin()
    #     return tx.serialize_as_bytes().hex()
    # ####

    def format_amount(self, x, is_diff=False, whitespaces=False):
        return util.format_satoshis(x, is_diff=is_diff, num_zeros=self.num_zeros, decimal_point=self.decimal_point,
                                    whitespaces=whitespaces)

    def base_unit(self):
        return util.decimal_point_to_base_unit_name(self.decimal_point)

    # set use unconfirmed coin
    def set_unconf(self, x):
        '''
        Enable/disable spend confirmed_only input, for btc only
        :param x: as bool
        :return:None
        '''
        self.config.set_key('confirmed_only', bool(x))

    # fiat balance
    def get_currencies(self):
        '''
        Get fiat list
        :return:json exp:{'CNY', 'USD'...}
        '''
        self._assert_daemon_running()
        currencies = sorted(self.daemon.fx.get_currencies(self.daemon.fx.get_history_config()))
        return json.dumps(currencies)

    def get_exchanges(self):
        '''
        Get exchange server list
        :return: json exp:{'exchanges', ...}
        '''
        if not self.daemon.fx: return
        b = self.daemon.fx.is_enabled()
        if b:
            h = self.daemon.fx.get_history_config()
            c = self.daemon.fx.get_currency()
            exchanges = self.daemon.fx.get_exchanges_by_ccy(c, h)
        else:
            exchanges = self.daemon.fx.get_exchanges_by_ccy('USD', False)
        return json.dumps(sorted(exchanges))

    def set_exchange(self, exchange):
        '''
        Set exchange server
        :param exchange: exchange server name as string like "exchanges"
        :return:None
        '''
        if self.daemon.fx and self.daemon.fx.is_enabled() and exchange and exchange != self.daemon.fx.exchange.name():
            self.daemon.fx.set_exchange(exchange)

    def set_currency(self, ccy):
        '''
        Set fiat
        :param ccy: fiat as string like "CNY"
        :return:None
        '''
        self.daemon.fx.set_enabled(True)
        if ccy != self.ccy:
            self.daemon.fx.set_currency(ccy)
            self.ccy = ccy
        self.update_status()

    def get_exchange_currency(self, type, amount):
        '''
        You can get coin to fiat or get fiat to coin
        :param type: base/fiat as str
        :param amount: value
        :return:
            exp:
                if you want get fiat from coin,like this:
                    get_exchange_currency("base", 1)
                return: 1,000.34 CNY

                if you want get coin from fiat, like this:
                    get_exchange_currency("fiat", 1000)
                return: 1 mBTC
        '''
        text = ""
        rate = self.daemon.fx.exchange_rate() if self.daemon.fx else Decimal('NaN')
        if rate.is_nan() or amount is None:
            return text
        else:
            if type == "base":
                amount = self.get_amount(amount)
                text = self.daemon.fx.ccy_amount_str(amount * Decimal(rate) / COIN, False)
            elif type == "fiat":
                text = self.format_amount((int(Decimal(amount) / Decimal(rate) * COIN)))
            return text

    def get_coin_exchange_currency(self, value, from_coin):
        try:
            last_price = PyWalib.get_coin_price(from_coin.upper())
            data = self.daemon.fx.format_amount_and_units(
                self.get_amount(value * last_price)) if self.daemon.fx else None
            return data
        except BaseException as e:
            raise e

    def set_base_uint(self, base_unit):
        '''
        Set base unit for(BTC/mBTC/bits/sat), for btc only
        :param base_unit: (BTC or mBTC or bits or sat) as string
        :return:None
        '''
        self.base_unit = base_unit
        self.decimal_point = util.base_unit_name_to_decimal_point(self.base_unit)
        self.config.set_key('decimal_point', self.decimal_point, True)
        self.update_status()

    def format_amount_and_units(self, amount):
        try:
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        text = self.format_amount(amount) + ' ' + self.base_unit
        x = self.daemon.fx.format_amount_and_units(amount) if self.daemon.fx else None
        if text and x:
            text += ' (%s)' % x
        return text

    # #proxy
    def set_proxy(self, proxy_mode, proxy_host, proxy_port, proxy_user, proxy_password):
        '''
        Set proxy server
        :param proxy_mode: SOCK4/SOCK5 as string
        :param proxy_host: server ip
        :param proxy_port: server port
        :param proxy_user: login user that you registed
        :param proxy_password: login password that you registed
        :return: raise except if error
        '''
        try:
            net_params = self.network.get_parameters()
            proxy = None
            if (proxy_mode != "" and proxy_host != "" and proxy_port != ""):
                proxy = {'mode': str(proxy_mode).lower(),
                         'host': str(proxy_host),
                         'port': str(proxy_port),
                         'user': str(proxy_user),
                         'password': str(proxy_password)}
            net_params = net_params._replace(proxy=proxy)
            self.network.run_from_another_thread(self.network.set_parameters(net_params))
        except BaseException as e:
            raise e

    # #qr api
    def get_raw_tx_from_qr_data(self, data):
        try:
            from electrum.util import bh2u
            return bh2u(base_decode(data, None, base=43))
        except BaseException as e:
            raise e

    def get_qr_data_from_raw_tx(self, raw_tx):
        try:
            from electrum.bitcoin import base_encode, bfh
            text = bfh(raw_tx)
            return base_encode(text, base=43)
        except BaseException as e:
            raise e

    def recover_tx_info(self, tx):
        try:
            tx = tx_from_any(str(tx))
            temp_tx = copy.deepcopy(tx)
            temp_tx.deserialize()
            temp_tx.add_info_from_wallet(self.wallet)
            return temp_tx
        except BaseException as e:
            raise e

    def get_tx_info_from_raw(self, raw_tx, tx_list=None):
        '''
        You can get detail info from a raw_tx, for btc only
        :param raw_tx: Raw tx as string
        :return: json like {'txid': ,
                            'can_broadcast': true,
                            'amount': "",
                            'fee': "",
                            'description': "",
                            'tx_status': "",
                            'sign_status': "",
                            'output_addr': "",
                            'input_addr': ["addr", ],
                            'height': 2000,
                            'cosigner': "",
                            'tx': "",
                            'show_status': [1, _("Unconfirmed")]}
        '''
        try:
            print("console:get_tx_info_from_raw:tx===%s" % raw_tx)
            tx = self.recover_tx_info(raw_tx)
        except Exception as e:
            tx = None
            raise BaseException(e)
        data = {}
        data = self.get_details_info(tx, tx_list=tx_list)
        return data

    def get_input_info(self, tx, all=None):
        input_list = []
        local_addr = self.txdb.get_received_tx_input_info(tx.txid())
        if len(local_addr) != 0:
            local_addr = json.loads(local_addr[0][1])
            if len(local_addr) != 0:
                return local_addr
        for txin in tx.inputs():
            input_info = {}
            addr = self.wallet.get_txin_address(txin)
            if addr is None:
                import asyncio
                try:
                    addr = asyncio.run_coroutine_threadsafe(
                        self.gettransaction(txin.prevout.txid.hex(), txin.prevout.out_idx),
                        self.network.asyncio_loop).result()
                except:
                    addr = ''
            input_info['address'] = addr
            input_list.append(input_info)
            if all is None:
                break
        self.txdb.add_received_tx_input_info(tx.txid(), json.dumps(input_list))
        return input_list

    def get_fee_from_server(self, txid):
        """Retrieve a transaction. """
        url_info = read_json('server_config.json', {})
        url_list = url_info['btc_server']
        for urlinfo in url_list:
            for key, url in urlinfo.items():
                url += txid
                try:
                    import requests
                    response = requests.get(url, timeout=2)
                    # response = await self.network.send_http_on_proxy("get", url, timeout=2)
                    response_json = response.json()
                except BaseException as e:
                    print(f"error....when get_btc_fee.....{e}")
                    pass
                    continue
                if response_json.__contains__('fee'):
                    return self.format_amount_and_units(response_json['fee'])
                elif response_json.__contains__('fees'):
                    return self.format_amount_and_units(response_json['fees'])
                else:
                    continue
        return ""

    # def get_receive_fee_by_hash(self, tx_hash):
    #     import asyncio
    #     try:
    #         fee = asyncio.run_coroutine_threadsafe(
    #             self.get_fee_from_server(tx_hash),
    #             self.network.asyncio_loop).result()
    #     except:
    #         fee = None
    #     return fee

    def get_details_info(self, tx, tx_list=None):
        try:
            self._assert_wallet_isvalid()
        except Exception as e:
            raise BaseException(e)
        tx_details = self.wallet.get_tx_info(tx)
        if 'Partially signed' in tx_details.status:
            temp_s, temp_r = tx.signature_count()
            s = int(temp_s / len(tx.inputs()))
            r = len(self.wallet.get_keystores())
        elif 'Unsigned' in tx_details.status:
            s = 0
            r = len(self.wallet.get_keystores())
        else:
            if self.wallet.wallet_type == "standard" or self.wallet.wallet_type == "imported":
                s = r = len(self.wallet.get_keystores())
            else:
                s, r = self.wallet.wallet_type.split("of", 1)
        in_list = self.get_input_info(tx)
        out_list = []
        for index, o in enumerate(tx.outputs()):
            address, value = o.address, o.value
            out_info = {}
            out_info['addr'] = address
            out_info['amount'] = self.format_amount_and_units(value)
            out_info['is_change'] = True if (index == len(tx.outputs()) - 1) and (len(tx.outputs()) != 1) else False
            out_list.append(out_info)

        amount_str = ""
        if tx_details.amount is None:
            amount_str = _("Transaction not related to the current wallet.")
        else:
            amount_str = self.format_amount_and_units(tx_details.amount)

        block_height = tx_details.tx_mined_status.height
        show_fee = ""
        if tx_details.fee is not None:
            show_fee = self.format_amount_and_units(tx_details.fee)
        else:
            if tx_list is None:
                show_fee_list = self.txdb.get_received_tx_fee_info(tx_details.txid)
                if len(show_fee_list) != 0:
                    show_fee = show_fee_list[0][1]
                if show_fee  == "":
                    show_fee = self.get_fee_from_server(tx_details.txid)
                    if show_fee != "":
                        self.txdb.add_received_tx_fee_info(tx_details.txid, show_fee)
        if block_height == -2:
            status = _("Unconfirmed")
            can_broadcast = False
        else:
            status = tx_details.status
            can_broadcast = tx_details.can_broadcast

        ret_data = {
            'txid': tx_details.txid,
            'can_broadcast': can_broadcast,
            'amount': amount_str,
            'fee': show_fee,
            # 'description': self.wallet.get_label(tx_details.txid) if 44 != int(self.wallet.keystore.get_derivation_prefix().split('/')[COIN_POS].split('\'')[0]) else "",
            'description': "",
            'tx_status': status,
            'sign_status': [s, r],
            'output_addr': out_list,
            'input_addr': in_list,
            'height': block_height,
            'cosigner': [x.xpub if not isinstance(x, Imported_KeyStore) else "" for x in self.wallet.get_keystores()],
            'tx': str(tx),
            'show_status': [1, _("Unconfirmed")] if (
                        block_height == 0 or (block_height < 0 and not can_broadcast)) else [3, _(
                "Confirmed")] if block_height > 0 else [2, _("Sending failure")]
        }
        json_data = json.dumps(ret_data)
        return json_data

    # invoices
    def delete_invoice(self, key):
        try:
            self._assert_wallet_isvalid()
            self.wallet.delete_invoice(key)
        except Exception as e:
            raise BaseException(e)

    def get_invoices(self):
        try:
            self._assert_wallet_isvalid()
            return self.wallet.get_invoices()
        except Exception as e:
            raise BaseException(e)

    def do_save(self, tx):
        try:
            if not self.wallet.add_transaction(tx):
                raise BaseException(
                    _(("Transaction cannot be saved. It conflicts with current history. tx=").format(tx.txid())))
        except BaseException as e:
            raise BaseException(e)
        else:
            self.wallet.save_db()

    def update_invoices(self, old_tx, new_tx):
        try:
            self._assert_wallet_isvalid()
            self.wallet.update_invoice(old_tx, new_tx)
        except Exception as e:
            raise BaseException(e)

    def clear_invoices(self):
        try:
            self._assert_wallet_isvalid()
            self.wallet.clear_invoices()
        except Exception as e:
            raise BaseException(e)

    ##history
    def get_history_tx(self):
        try:
            self._assert_wallet_isvalid()
        except Exception as e:
            raise BaseException(e)
        history = reversed(self.wallet.get_history())
        all_data = [self.get_card(*item) for item in history]
        return all_data

    # get input address for receive tx
    async def gettransaction(self, txid, n):
        """Retrieve a transaction. """
        tx = None
        raw = await self.network.get_transaction(txid, timeout=3)
        if raw:
            tx = Transaction(raw)
        else:
            raise Exception("Unknown transaction")
        if tx.txid() != txid:
            raise Exception("Mismatching txid")
        addr = tx._outputs[n].address
        return addr

    def get_btc_tx_list(self, start=None, end=None, search_type=None):
        history_data = []
        try:
            history_info = self.get_history_tx()
            local_tx = self.txdb.get_tx_info(self.wallet.get_addresses()[0])
        except BaseException as e:
            raise e

        if search_type is None:
            history_data = history_info
        elif search_type == 'send':
            for info in history_info:
                if info['is_mine']:
                    history_data.append(info)
        elif search_type == 'receive':
            for info in history_info:
                if not info['is_mine']:
                    history_data.append(info)

        history_len = len(history_data)
        local_len = len(local_tx)
        all_tx_len = history_len + local_len
        if start is None or end is None:
            start = 0
            if 'receive' == search_type:
                end = history_len
            else:
                end = all_tx_len
        if (search_type is None or 'send' in search_type) and all_tx_len == self.old_history_len:
            return json.dumps(self.old_history_info[start:end])

        all_data = []
        if search_type == "receive":
            for pos, info in enumerate(history_data):
                if pos >= start and pos <= end:
                    self.get_history_show_info(info, all_data)
            return json.dumps(all_data)
        else:
            self.old_history_len = all_tx_len
            for info in history_data:
                self.get_history_show_info(info, all_data)

           # local_tx = self.txdb.get_tx_info(self.wallet.get_addresses()[0])
            for info in local_tx:
                i = {}
                i['type'] = 'history'
                data = self.get_tx_info_from_raw(info[3], tx_list=True)
                i['tx_status'] = _("Sending failure")
                i['date'] = util.format_time(int(info[4]))
                i['tx_hash'] = info[0]
                i['is_mine'] = True
                i['confirmations'] = 0
                data = json.loads(data)
                i['address'] = self.get_show_addr(data['output_addr'][0]['addr'])
                amount = data['amount'].split(" ")[0]
                if amount[0] == '-':
                    amount = amount[1:]
                fee = data['fee'].split(" ")[0]
                fait = self.daemon.fx.format_amount_and_units(float(amount) + float(fee)) if self.daemon.fx else None
                show_amount = '%.8f' % (float(amount) + float(fee))
                show_amount = str(show_amount).rstrip('0')
                if show_amount[-1] == '.':
                    show_amount = show_amount[0:-1]
                i['amount'] = '%s %s (%s)' % (show_amount, self.base_unit, fait)
                all_data.append(i)

            all_data.sort(reverse=True, key=lambda info: info['date'])
            self.old_history_info = all_data
            return json.dumps(all_data[start:end])

    ##get history
    def get_eth_tx_list(self, wallet_obj, search_type=None):
        txs = PyWalib.get_transaction_history(wallet_obj.get_addresses()[0], search_type=search_type)
        for tx in txs:
            fiat = tx["fiat"]
            fiat = self.daemon.fx.format_amount_and_units(fiat * COIN) or f"0 {self.ccy}"
            amount = tx["amount"]
            amount = f"{amount} ETH ({fiat})"
            tx["amount"] = amount
            tx.pop("fiat", None)

        return txs

    def get_detail_tx_info_by_hash(self, tx_hash):
        try:
            self._assert_wallet_isvalid()
            tx = self.get_btc_raw_tx(tx_hash)
            tx_details = self.wallet.get_tx_info(tx)
            in_list = self.get_input_info(tx, all=True)
            out_list = []
            for index, o in enumerate(tx.outputs()):
                address, value = o.address, o.value
                out_info = {}
                out_info['addr'] = address
                out_info['amount'] = self.format_amount_and_units(value)
                out_list.append(out_info)
            out = {
                "input_list" : in_list,
                "output_list" : out_list
            }
            return json.dumps(out)
        except BaseException as e:
            raise e

    def get_all_tx_list(self, search_type=None, coin='btc', start=None, end=None):
        '''
        Get the histroy list with the wallet that you select
        :param search_type: None/send/receive as str
        :param coin: btc/eth as string
        :param start: start position as int
        :param end: end position as int
        :return:
            exp:
                [{"type":"",
                 "tx_status":"",
                 "date":"",
                 "tx_hash":"",
                 "is_mine":"",
                 "confirmations":"",
                 "address":"",
                 "amount":""}, ...]
        '''
        try:
            if coin == "btc":
                return self.get_btc_tx_list(start=start, end=end, search_type=search_type)
            else:
                return self.get_eth_tx_list(self.wallet, search_type=search_type)
        except BaseException as e:
            raise e

    def get_show_addr(self, addr):
        return '%s...%s' % (addr[0:6], addr[-6:])

    def get_history_show_info(self, info, list_info):
        info['type'] = 'history'
        data = self.get_tx_info(info['tx_hash'], tx_list=True)
        info['tx_status'] = json.loads(data)['tx_status']
        info['address'] = self.get_show_addr(json.loads(data)['output_addr'][0]['addr']) if info[
            'is_mine'] else self.get_show_addr(json.loads(data)['input_addr'][0]['address'])
        time = self.txdb.get_tx_time_info(info['tx_hash'])
        if len(time) != 0:
            info['date'] = util.format_time(int(time[0][1]))
        list_info.append(info)
 
    def get_btc_raw_tx(self, tx_hash):
        try:
            self._assert_wallet_isvalid()
        except Exception as e:
            raise BaseException(e)
        tx = self.wallet.db.get_transaction(tx_hash)
        if not tx:
            local_tx = self.txdb.get_tx_info(self.wallet.get_addresses()[0])
            for temp_tx in local_tx:
                if temp_tx[0] == tx_hash:
                    return self.get_tx_info_from_raw(temp_tx[3])
            raise BaseException(_('Failed to get transaction details.'))
        # tx = PartialTransaction.from_tx(tx)
        label = self.wallet.get_label(tx_hash) or None
        tx = copy.deepcopy(tx)
        try:
            tx.deserialize()
        except Exception as e:
            raise e
        tx.add_info_from_wallet(self.wallet)
        return tx

    def get_tx_info(self, tx_hash, coin="btc", tx_list=None):
        '''
        Get detail info by tx_hash
        :param tx_hash: tx_hash as string
        :param tx_list: Not for app
        :param coin: btc/eth
        :return:
            Json like {'txid': ,
                    'can_broadcast': true,
                    'amount': "",
                    'fee': "",
                    'description': "",
                    'tx_status': "",
                    'sign_status': "",
                    'output_addr': "",
                    'input_addr': ["addr", ],
                    'height': 2000,
                    'cosigner': "",
                    'tx': "",
                    'show_status': [1, _("Unconfirmed")]}
        '''
        try:
            self._assert_wallet_isvalid()
        except Exception as e:
            raise BaseException(e)

        if coin in self.coins:
            return self.get_eth_tx_info(tx_hash)

        tx = self.get_btc_raw_tx(tx_hash)
        return self.get_details_info(tx, tx_list=tx_list)

    def get_eth_tx_info(self, tx_hash) -> str:
        tx = self.pywalib.get_transaction_info(tx_hash)
        fiat = self.daemon.fx.format_amount_and_units(tx.pop("fiat") * COIN) or f"0 {self.ccy}"
        fee_fiat = self.daemon.fx.format_amount_and_units(tx.pop("fee_fiat") * COIN) or f"0 {self.ccy}"
        tx["amount"] = f"{tx['amount']} ETH ({fiat})"
        tx["fee"] = f"{tx['fee']} ETH ({fee_fiat})"

        return json.dumps(tx, cls=DecimalEncoder)

    def get_card(self, tx_hash, tx_mined_status, delta, fee, balance):
        try:
            self._assert_wallet_isvalid()
            self._assert_daemon_running()
        except Exception as e:
            raise BaseException(e)
        status, status_str = self.wallet.get_tx_status(tx_hash, tx_mined_status)
        label = self.wallet.get_label(tx_hash) if tx_hash else ""
        ri = {}
        ri['tx_hash'] = tx_hash
        ri['date'] = status_str
        ri['message'] = label
        ri['confirmations'] = tx_mined_status.conf
        if delta is not None:
            ri['is_mine'] = delta < 0
            if delta < 0: delta = - delta
            ri['amount'] = self.format_amount_and_units(delta)
            if self.fiat_unit:
                fx = self.daemon.fx
                fiat_value = delta / Decimal(bitcoin.COIN) * self.wallet.price_at_timestamp(tx_hash, fx.timestamp_rate)
                fiat_value = Fiat(fiat_value, fx.ccy)
                ri['quote_text'] = fiat_value.to_ui_string()
        return ri

    def get_wallet_address_show_UI(self, next=None):
        '''
        Get receving address, for btc only
        :param next: if you want change address, you can set the param
        :return: json like {'qr_data':"", "addr":""}
        '''
        try:
            self._assert_wallet_isvalid()
            show_addr_info = self.config.get("show_addr_info", {})
            if show_addr_info.__contains__(self.wallet.__str__()):
                self.show_addr = show_addr_info[self.wallet.__str__()]
            else:
                self.show_addr = self.wallet.get_addresses()[0]
            if next:
                addr = self.wallet.create_new_address(False)
                self.show_addr = addr
                show_addr_info[self.wallet.__str__()] = self.show_addr
                self.config.set_key("show_addr_info", show_addr_info)

            if bitcoin.is_address(self.show_addr):
                data = util.create_bip21_uri(self.show_addr, "", "")
            elif self.pywalib.web3.isAddress(self.show_addr):
                data = f"ethereum:{self.show_addr}"
            else:
                data = self.show_addr
        except Exception as e:
            raise BaseException(e)
        data_json = {}
        data_json['qr_data'] = data
        data_json['addr'] = self.show_addr
        return json.dumps(data_json)

    def get_all_funded_address(self):
        '''
        Get a address list of have balance, for btc only
        :return: json like [{"address":"", "balance":""},...]
        '''
        try:
            self._assert_wallet_isvalid()
            all_addr = self.wallet.get_addresses()
            funded_addrs_list = []
            for addr in all_addr:
                c, u, x = self.wallet.get_addr_balance(addr)
                balance = c + u + x
                if balance == 0:
                    continue
                funded_addr = {}
                funded_addr['address'] = addr
                funded_addr['balance'] = self.format_amount_and_units(balance)
                funded_addrs_list.append(funded_addr)
            return json.dumps(funded_addrs_list)
        except Exception as e:
            raise BaseException(e)

    def get_unspend_utxos(self):
        '''
        Get unspend utxos if you need
        :return:
        '''
        try:
            coins = []
            for txin in self.wallet.get_utxos():
                d = txin.to_json()
                dust_sat = int(d['value_sats'])
                v = d.pop("value_sats")
                d["value"] = self.format_amount(v) + ' ' + self.base_unit
                if dust_sat <= 546:
                    if self.config.get('dust_flag', True):
                        continue
                coins.append(d)
                self.coins.append(txin)
            return coins
        except BaseException as e:
            raise e

    def save_tx_to_file(self, path, tx):
        '''
        Save the psbt/tx to path
        :param path: path as string
        :param tx: raw tx as string
        :return: raise except if error
        '''
        try:
            if tx is None:
                raise BaseException("The tx cannot be empty")
            tx = tx_from_any(tx)
            if isinstance(tx, PartialTransaction):
                tx.finalize_psbt()
            if tx.is_complete():  # network tx hex
                path += '.txn'
                with open(path, "w+") as f:
                    network_tx_hex = tx.serialize_to_network()
                    f.write(network_tx_hex + '\n')
            else:  # if partial: PSBT bytes
                assert isinstance(tx, PartialTransaction)
                path += '.psbt'
                with open(path, "wb+") as f:
                    f.write(tx.serialize_as_bytes())
        except Exception as e:
            raise BaseException(e)

    def read_tx_from_file(self, path: str, is_tx=True) -> str:
        """
        Import tx info from path
        :param is_tx: if True psbt tx else message
        :param path: path as string
        :return: serialized tx or file content
        """
        try:
            with open(path, "rb" if is_tx else "r") as f:
                file_content = f.read()
                if is_tx:
                    tx = tx_from_any(file_content)
        except (ValueError, IOError, os.error) as reason:
            raise BaseException(_("Failed to open file."))
        else:
            return tx if is_tx else file_content

    ##Analyze QR data
    def parse_address(self, data):
        data = data.strip()
        try:
            out = util.parse_URI(data)

            r = out.get('r')
            sig = out.get('sig')
            name = out.get('name')

            if r or (name and sig):
                if name and sig:
                    s = paymentrequest.serialize_request(out).SerializeToString()
                    result = paymentrequest.PaymentRequest(s)
                else:
                    result = asyncio.run_coroutine_threadsafe(
                        paymentrequest.get_payment_request(r),
                        self.network.asyncio_loop).result()

                out = {'address': result.get_address(), 'memo': result.get_memo()}
                if result.get_amount() != 0:
                    out['amount'] = result.get_amount()

            return out
        except Exception as e:
            raise Exception(e)

    def parse_tx(self, data):
        # try to decode transaction
        from electrum.transaction import Transaction
        from electrum.util import bh2u
        try:
            # text = bh2u(base_decode(data, base=43))
            tx = self.recover_tx_info(data)
        except Exception as e:
            tx = None
            raise BaseException(e)

        data = self.get_details_info(tx)
        return data

    def parse_pr(self, data):
        '''
        Parse qr code which generated by address or tx, for btc only
        :param data: qr cata as str
        :return:
            if data is address qr data, return data like:
                {"type": 1, "data":"bcrt1qzm6y9j0zg9nnludkgtc0pvhet0sf76szjw7fjw"}
            if data is tx qr data, return data like:
                {"type": 2, "data":"02000000000103f9f51..."}
            if data is not address and not tx, return data like:
                {"type": 3, "data":"parse pr error"}

        '''
        add_status_flag = False
        tx_status_flag = False
        add_data = {}
        try:
            add_data = self.parse_address(data)
            add_status_flag = True
        except BaseException as e:
            add_status_flag = False

        try:
            tx_data = self.parse_tx(data)
            tx_status_flag = True
        except BaseException as e:
            tx_status_flag = False

        out_data = {}
        if (add_status_flag):
            out_data['type'] = 1
            out_data['data'] = add_data
        elif (tx_status_flag):
            out_data['type'] = 2
            out_data['data'] = json.loads(tx_data)
        else:
            out_data['type'] = 3
            out_data['data'] = "parse pr error"
        return json.dumps(out_data, cls=DecimalEncoder)

    def update_local_info(self, txid, address, tx, msg):
        self.remove_local_tx(txid)
        self.txdb.add_tx_info(address=address, tx_hash=txid, psbt_tx="", raw_tx=tx,
                              failed_info=msg)

    def broadcast_tx(self, tx: str) -> str:
        """
        Broadcast the tx, for btc only
        :param tx: tx as string
        :return: 'success'
        :raise: BaseException
        """
        trans = None
        try:
            if isinstance(tx, str):
                trans = tx_from_any(tx)
                trans.deserialize()
            if self.network and self.network.is_connected():
                self.network.run_from_another_thread(self.network.broadcast_transaction(trans))
            else:
                self.txdb.add_tx_time_info(trans.txid())
                raise BaseException(_('Cannot broadcast transaction due to network connected exceptions'))
        except SerializationError:
            raise BaseException(_('Transaction formatter error'))
        except TxBroadcastError as e:
            msg = e.get_message_for_gui()
            self.update_local_info(trans.txid(), self.wallet.get_addresses()[0], tx, msg)
            raise BaseException(msg)
        except BestEffortRequestFailed as e:
            msg = str(e)
            raise BaseException(msg)
        else:
            return "success"
        finally:
            if trans:
                self.txdb.add_tx_time_info(trans.txid())

    def set_use_change(self, status_change):
        '''
        Enable/disable change address, for btc only
        :param status_change: as bool
        :return: raise except if error
        '''
        try:
            self._assert_wallet_isvalid()
        except Exception as e:
            raise BaseException(e)
        if self.wallet.use_change == status_change:
            return
        self.config.set_key('use_change', status_change, False)
        self.wallet.use_change = status_change

    def sign_message(self, address, message, path='android_usb', password=None):
        '''
        Sign message, for btc only
        :param address: must bitcoin address, must in current wallet as string
        :param message: message need be signed as string
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :param password: as string
        :return: Base64 encrypted as string
        '''
        try:
            if path:
                self.get_client(path=path)
            self._assert_wallet_isvalid()
            address = address.strip()
            message = message.strip()
            if not bitcoin.is_address(address):
                raise BaseException(UnavailableBtcAddr())
            if self.wallet.is_watching_only():
                raise BaseException(_('This is a watching-only wallet.'))
            if not self.wallet.is_mine(address):
                raise BaseException(_('The address is not in the current wallet.'))
            txin_type = self.wallet.get_txin_type(address)
            if txin_type not in ['p2pkh', 'p2wpkh', 'p2wpkh-p2sh']:
                raise BaseException(_('Current wallet does not support signature message:{}'.format(txin_type)))
            sig = self.wallet.sign_message(address, message, password)
            import base64
            return base64.b64encode(sig).decode('ascii')
        except Exception as e:
            raise BaseException(e)

    def verify_message(self, address, message, signature):
        '''
        Verify the message that you signed by sign_message, for btc only
        :param address: must bitcoin address as str
        :param message: message as str
        :param signature: sign info retured by sign_message api
        :return: true/false as bool
        '''
        address = address.strip()
        message = message.strip().encode('utf-8')
        if not bitcoin.is_address(address):
            raise BaseException(UnavailableBtcAddr())
        try:
            # This can throw on invalid base64
            import base64
            sig = base64.b64decode(str(signature))
            verified = ecc.verify_message_with_address(address, sig, message)
        except Exception as e:
            verified = False
        return verified

    def add_token(self, symbol, contract_addr):
        '''
        Add token to eth, for eth/bsc only
        :param symbol: coin symbol
        :param contract_addr: coin address
        :return: raise except if error
        '''
        try:
            self.wallet.add_contract_token(symbol, contract_addr)
        except BaseException as e:
            raise e

    def delete_token(self, contract_addr):
        '''
        Delete token from current wallet, for eth/bsc only
        :param contract_addr: coin address
        :return: raise except if error
        '''
        try:
            self.wallet.delete_contract_token(contract_addr)
        except BaseException as e:
            raise e

    ###############
    def sign_eth_tx(self, to_addr, value, path='android_usb', password=None, contract_addr=None, gas_price=None, gas_limit=None, data=None):
        '''
        Send for eth, for eth/bsc only
        :param to_addr: as string
        :param value: amount to send
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :param password: as string
        :param contract_addr: need if send to contranct
        :param gas_price: as string, unit is Gwei
        :param gas_limit: as string
        :param data: eth tx custom data, as hex string
        :return: tx_hash as string
        '''
        from_address = self.wallet.get_addresses()[0]
        if contract_addr is None:
            tx_dict = self.pywalib.get_transaction(from_address, to_addr, value, gas_price=gas_price, gas_limit=gas_limit, data=data)
        else:
            contract_addr = self.pywalib.web3.toChecksumAddress(contract_addr)
            contract = self.wallet.get_contract_token(contract_addr)
            assert contract is not None
            tx_dict = self.pywalib.get_transaction(from_address, to_addr, value, contract=contract,
                                                   gas_price=gas_price, gas_limit=gas_limit, data=data)
        if isinstance(self.wallet.get_keystore(), Hardware_KeyStore):
            if path:
                client = self.get_client(path=path)
                deriv_suffix = self.wallet.get_address_index(from_address)
                derivation = self.wallet.keystore.get_derivation_prefix()
                address_path = "%s/%d/%d" % (derivation, *deriv_suffix)
                address_n = parse_path(address_path)

                (v, r, s) = self.wallet.sign_transaction(address_n, tx_dict['nonce'], tx_dict['gasPrice'],
                                               tx_dict['gas'],
                                               str(tx_dict['to']),
                                               tx_dict['value'], data=(tx_dict['data'] if contract_addr is not None else None),
                                               chain_id=tx_dict['chainId'])
                from eth_utils.encoding import big_endian_to_int
                r = big_endian_to_int(r)
                s = big_endian_to_int(s)
                return self.pywalib.serialize_and_send_tx(tx_dict, vrs=(v, r, s))
        else:
            return self.pywalib.sign_and_send_tx(self.wallet.get_account(from_address, password), tx_dict)

    def sign_tx(self, tx, path=None, password=None):
        '''
        Sign one transaction, for btc only
        :param tx: tx info as str
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :param password: password as str
        :return: signed tx on success and raise except on failure
        '''
        try:
            if path is not None:
                self.get_client(path=path)
            self._assert_wallet_isvalid()
            tx = tx_from_any(tx)
            tx.deserialize()
            sign_tx = self.wallet.sign_transaction(tx, password)
            print("=======sign_tx.serialize=%s" % sign_tx.serialize_as_bytes().hex())
            try:
                if self.label_flag and self.wallet.wallet_type != "standard":
                    self.label_plugin.push_tx(self.wallet, 'signtx', tx.txid(), str(sign_tx))
            except BaseException as e:
                print(f"push_tx signtx error {e}")
                pass
            return self.get_tx_info_from_raw(sign_tx)
        except BaseException as e:
            # msg = e.__str__()
            # self.update_local_info(tx.txid(), self.wallet.get_addresses()[0], tx, msg)
            raise BaseException(e)

    def get_derived_list(self, xpub):
        try:
            derived_info = DerivedInfo(self.config)
            derived_info.init_recovery_num()
            for derived_xpub, wallet_list in self.derived_info.items():
                if xpub == derived_xpub:
                    for info in wallet_list:
                        derived_info.update_recovery_info(info['account_id'])
                    derived_info.reset_list()
                    return derived_info.get_list()
            return None
        except BaseException as e:
            raise e


    ##connection with terzorlib#########################
    def hardware_verify(self, msg, path='android_usb'):
        '''
        Anti-counterfeiting verification, used by hardware
        :param msg: msg as str
        :param path: NFC/android_usb/bluetooth as str
        :return: json like {"data":, "cert":, "signature":,}
        '''
        client = self.get_client(path=path)
        try:
            from hashlib import sha256
            digest = sha256(msg.encode("utf-8")).digest()
            response = device.se_verify(client.client, digest)
        except Exception as e:
            raise BaseException(e)
        verify_info = {}
        verify_info['data'] = msg
        verify_info['cert'] = response.cert.hex()
        verify_info['signature'] = response.signature.hex()
        return json.dumps(verify_info)

    def backup_wallet(self, path='android_usb'):
        '''
        Backup wallet by se
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :return:
        '''
        client = self.get_client(path=path)
        try:
            response = client.backup()
        except Exception as e:
            raise BaseException(e)
        return response

    def se_proxy(self, message, path='android_usb') -> str:
        client = self.get_client(path=path)
        try:
            response = client.se_proxy(message)
        except Exception as e:
            raise BaseException(e)
        return response

    def bixin_backup_device(self, path='android_usb'):
        '''
        Export seed, used by hardware
        :param path: NFC/android_usb/bluetooth as str
        :return: as string
        '''
        client = self.get_client(path=path)
        try:
            response = client.bixin_backup_device()
        except Exception as e:
            raise BaseException(e)
        print(f"seed......{response}")
        return response

    def bixin_load_device(self, path='android_usb', mnemonics=None, language="en-US", label="BIXIN KEY"):
        '''
        Import seed, used by hardware
        :param mnemonics: as string
        :param path: NFC/android_usb/bluetooth as str
        :return: raise except if error
        '''
        client = self.get_client(path=path)
        try:
            response = client.bixin_load_device(mnemonics=mnemonics, language=language, label=label)
        except Exception as e:
            raise BaseException(e)
        return response

    def recovery_wallet_hw(self, path='android_usb', *args):
        '''
        Recovery wallet by encryption
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :param args: encryption data as str
        :return:
        '''
        client = self.get_client(path=path)
        try:
            response = client.recovery(*args)
        except Exception as e:
            raise BaseException(e)
        return response

    def bx_inquire_whitelist(self, path='android_usb', **kwargs):
        '''
        Inquire
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :param kwargs:
            type:2=inquire
            addr_in: addreess as str
        :return:
        '''
        client = self.get_client(path=path)
        try:
            response = json.dumps(client.bx_inquire_whitelist(**kwargs))
        except Exception as e:
            raise BaseException(e)
        return response

    def bx_add_or_delete_whitelist(self, path='android_usb', **kwargs):
        '''
        Add and delete whitelist
        :param path: NFC/android_usb/bluetooth as str, used by hardware
        :param kwargs:
            type:0=add 1=delete
            addr_in: addreess as str
        :return:
        '''
        client = self.get_client(path=path)
        try:
            response = json.dumps(client.bx_add_or_delete_whitelist(**kwargs))
        except Exception as e:
            raise BaseException(e)
        return response

    def apply_setting(self, path='nfc', **kwargs):
        '''
        Set the hardware function, used by hardware
        :param path: NFC/android_usb/bluetooth as str
        :param kwargs:
            label="wangls"
            language="chinese/english"
            use_passphrase=true/false
            auto_lock_delay_ms="600"
            use_ble=true/false
            use_se=true/false
            is_bixinapp=true/false
        :return:0/1
        '''
        client = self.get_client(path=path)
        try:
            resp = device.apply_settings(client.client, **kwargs)
            if resp == "Settings applied":
                return 1
            else:
                return 0
        except Exception as e:
            raise BaseException(e)

    def init(self, path='android_usb', label="BIXIN KEY", language='english', stronger_mnemonic=None, use_se=False):
        '''
        Activate the device, used by hardware
        :param stronger_mnemonic: if not None 256  else 128
        :param path: NFC/android_usb/bluetooth as str
        :param label: name as string
        :param language: as string
        :param use_se: as bool
        :return:0/1
        '''
        client = self.get_client(path=path)
        strength = 128
        try:
            if use_se:
                device.apply_settings(client.client, use_se=True)
            if stronger_mnemonic:
                strength = 256
            response = client.reset_device(language=language, passphrase_protection=True, label=label,
                                           strength=strength)
        except Exception as e:
            raise BaseException(e)
        if response == "Device successfully initialized":
            return 1
        else:
            return 0

    def reset_pin(self, path='android_usb') -> int:
        '''
        Reset pin, used by hardware
        :param path:NFC/android_usb/bluetooth as str
        :return:0/1
        '''
        try:
            client = self.get_client(path)
            resp = client.set_pin(False)
        except Exception as e:
            if isinstance(e, exceptions.PinException) or isinstance(e, RuntimeError):
                return 0
            else:
                raise BaseException("user cancel")
        return 1
        # if resp == "PIN changed":
        #     return 1
        # else:
        #     return -1

    def show_address(self, address, path='android_usb', coin='btc') -> str:
        '''
        Verify address on hardware, used by hardware
        :param address: address as str
        :param path: NFC/android_usb/bluetooth as str
        :return:1/except
        '''
        try:
            plugin = self.plugin.get_plugin("trezor")
            plugin.show_address(path=path, ui=CustomerUI(), wallet=self.wallet, address=address, coin=coin)
            return "1"
        except Exception as e:
            raise BaseException(e)

    def wipe_device(self, path='android_usb') -> int:
        '''
        Reset device, used by hardware
        :param path: NFC/android_usb/bluetooth as str
        :return:0/1
        '''
        try:
            client = self.get_client(path)
            resp = client.wipe_device()
        except Exception as e:
            if isinstance(e, exceptions.PinException) or isinstance(e, RuntimeError):
                return 0
            else:
                raise BaseException(str(e)) from e
        if resp == "Device wiped":
            return 1
        else:
            return 0

    def get_passphrase_status(self, path='android_usb'):
        # self.client = None
        # self.path = ''
        client = self.get_client(path=path)
        return client.features.passphrase_protection

    def get_client(self, path='android_usb', ui=CustomerUI(), clean=False):
        plugin = self.plugin.get_plugin("trezor")
        if clean:
            plugin.clean()
        return plugin.get_client(path=path, ui=ui)

    def is_encrypted_with_hw_device(self):
        ret = self.wallet.storage.is_encrypted_with_hw_device()
        print(f"hw device....{ret}")

    def get_feature(self, path='android_usb'):
        '''
        Get hardware information, used by hardware
        :param path: NFC/android_usb/bluetooth as str
        :return: dict like:
            {capabilities: List[EnumTypeCapability] = None,
            vendor: str = None,
            major_version: int = None,  主版本号
            minor_version: int = None,  次版本号
            patch_version: int = None,  修订号    硬件的软件版本(俗称固件，在2.0.1 之前使用)
            bootloader_mode: bool = None,  设备当时是不是在bootloader模式
            device_id: str = None,  设备唯一标识，设备恢复出厂设置这个值会变
            pin_protection: bool = None, 是否开启了PIN码保护，
            passphrase_protection: bool = None, 是否开启了passphrase功能,这个用来支持创建隐藏钱包
            language: str = None,
            label: str = None,  激活钱包时，使用的名字
            initialized: bool = None, 当时设备是否激活
            revision: bytes = None,
            bootloader_hash: bytes = None,
            imported: bool = None,
            unlocked: bool = None,
            firmware_present: bool = None,
            needs_backup: bool = None,
            flags: int = None,
            model: str = None,
            fw_major: int = None,
            fw_minor: int = None,
            fw_patch: int = None,
            fw_vendor: str = None,
            fw_vendor_keys: bytes = None,
            unfinished_backup: bool = None,
            no_backup: bool = None,
            recovery_mode: bool = None,
            backup_type: EnumTypeBackupType = None,
            sd_card_present: bool = None,
            sd_protection: bool = None,
            wipe_code_protection: bool = None,
            session_id: bytes = None,
            passphrase_always_on_device: bool = None,
            safety_checks: EnumTypeSafetyCheckLevel = None,
            auto_lock_delay_ms: int = None, 自动关机时间
            display_rotation: int = None,
            experimental_features: bool = None,
            offset: int = None,  升级时断点续传使用的字段
            ble_name: str = None,
            ble_ver: str = None,  蓝牙固件版本
            ble_enable: bool = None,
            se_enable: bool = None,
            se_ver: str = None,  se的版本
            backup_only: bool = None,  是否是特殊设备，只用来备份，没有额外功能支持
            onekey_version: str = None,  硬件的软件版本（俗称固件），仅供APP使用（从2.0.1开始加入）}
        '''
        with self.lock:
            client = self.get_client(path=path, clean=True)
        return json.dumps(protobuf.to_dict(client.features))

    def get_xpub_from_hw(self, path='android_usb', _type='p2wpkh', is_creating=True, account_id=None, coin='btc'):
        '''
        Get extended public key from hardware, used by hardware
        :param path: NFC/android_usb/bluetooth as str
        :param _type: p2wsh/p2pkh/p2pkh-p2sh as string
        :coin: btc/eth as string
        :return: xpub string
        '''
        client = self.get_client(path=path)
        self.hw_info['device_id'] = client.features.device_id
        self.hw_info['type'] = _type
        if account_id is None:
            account_id = 0
        if coin == 'btc':
            if _type == "p2wsh":
                derivation = purpose48_derivation(account_id, xtype='p2wsh')
                self.hw_info['type'] = 48
                # derivation = bip44_derivation(account_id, bip43_purpose=48)
            elif _type == 'p2wpkh':
                derivation = bip44_derivation(account_id, bip43_purpose=84)
                self.hw_info['type'] = 84
            elif _type == 'p2pkh':
                derivation = bip44_derivation(account_id, bip43_purpose=44)
                self.hw_info['type'] = 44
            elif _type == 'p2wpkh-p2sh':
                derivation = bip44_derivation(account_id, bip43_purpose=49)
                self.hw_info['type'] = 49
            try:
                xpub = client.get_xpub(derivation, _type, creating=is_creating)
            except Exception as e:
                raise BaseException(e)
            return xpub
        else:
            derivation = bip44_derivation(account_id, bip43_purpose=self.coins[coin]['addressType'],
                                          coin=self.coins[coin]['coinId'])
            try:
                xpub = client.get_eth_xpub(derivation)
                self.hw_info['type'] = self.coins[coin]['addressType']
            except Exception as e:
                raise BaseException(e)
            return xpub

    def create_hw_derived_wallet(self, path='android_usb', _type='p2wpkh', is_creating=True, coin='btc'):
        '''
        Create derived wallet by hardware, used by hardware
        :param path: NFC/android_usb/bluetooth as string
        :param _type: p2wsh/p2wsh/p2pkh/p2pkh-p2sh as string
        :coin: btc/eth as string
        :return: xpub as string
        '''
        xpub = self.get_xpub_from_hw(path=path, _type='p2wpkh', coin=coin)
        list_info = self.get_derived_list(xpub)
        self.hw_info['xpub'] = xpub
        if list_info is None:
            self.hw_info['account_id'] = 0
            dervied_xpub = self.get_xpub_from_hw(path=path, _type=_type, account_id=0, coin=coin)
            return dervied_xpub
        if len(list_info) == 0:
            raise BaseException(DerivedWalletLimit())
        dervied_xpub = self.get_xpub_from_hw(path=path, _type=_type, account_id=list_info[0], coin=coin)
        self.hw_info['account_id'] = list_info[0]
        return dervied_xpub

    def firmware_update(
            self,
            filename,
            path,
            type="",
            fingerprint=None,
            skip_check=True,
            raw=False,
            dry_run=False,
    ):
        '''
        Upload new firmware to device.used by hardware
        Note : Device must be in bootloader mode.
        :param filename: full path to local upgrade file
        :param path: NFC/android_usb/bluetooth as str
        :return: None
        '''
        # self.client = None
        # self.path = ''
        client = self.get_client(path)
        features = client.features
        if not features.bootloader_mode:
            resp = client.client.call(messages.BixinReboot())
            if not dry_run and not isinstance(resp, messages.Success):
                raise RuntimeError("Failed turn into bootloader")
            time.sleep(2)
            client.client.init_device()
        if not dry_run and not client.features.bootloader_mode:
            raise BaseException(_("Switch the device to bootloader mode."))

        bootloader_onev2 = features.major_version == 1 and features.minor_version >= 8

        if filename:
            try:
                if type:
                    data = parse(filename)
                else:
                    with open(filename, "rb") as file:
                        data = file.read()
            except Exception as e:
                raise BaseException(e)
        else:
            raise BaseException(("Please give the file name"))

        if not raw and not skip_check:
            try:
                version, fw = firmware.parse(data)
            except Exception as e:
                raise BaseException(e)

            trezorctl.validate_firmware(version, fw, fingerprint)
            if (
                    bootloader_onev2
                    and version == firmware.FirmwareFormat.TREZOR_ONE
                    and not fw.embedded_onev2
            ):
                raise BaseException(_("Your device's firmware version is too low. Aborting."))
            elif not bootloader_onev2 and version == firmware.FirmwareFormat.TREZOR_ONE_V2:
                raise BaseException(_("You need to upgrade the bootloader immediately."))

            if features.major_version not in trezorctl.ALLOWED_FIRMWARE_FORMATS:
                raise BaseException(("Trezorctl doesn't know your device version. Aborting."))
            elif version not in trezorctl.ALLOWED_FIRMWARE_FORMATS[features.major_version]:
                raise BaseException(_("Firmware not compatible with your equipment, Aborting."))

        if not raw:
            # special handling for embedded-OneV2 format:
            # for bootloader < 1.8, keep the embedding
            # for bootloader 1.8.0 and up, strip the old OneV1 header
            if bootloader_onev2 and data[:4] == b"TRZR" and data[256: 256 + 4] == b"TRZF":
                print("Extracting embedded firmware image (fingerprint may change).")
                data = data[256:]

        if dry_run:
            print("Dry run. Not uploading firmware to device.")
        else:
            try:
                if features.major_version == 1 and features.firmware_present is not False:
                    # Trezor One does not send ButtonRequest
                    print("Please confirm the action on your Trezor device")
                return firmware.update(client.client, data, type=type)
            except exceptions.Cancelled:
                print("Update aborted on device.")
            except exceptions.TrezorException as e:
                raise BaseException(_("Update failed: {}".format(e)))

    ####################################################
    # app wallet
    def export_keystore(self, password):
        '''
        Export keystory from eth wallet, for eth only
        :param password: password as string
        :return:Keystore info for success/exception info for fuilure
        '''
        try:
            address = self.wallet.get_addresses()[0]
            keystore = self.wallet.export_keystore(address, password=password)
            return json.dumps(keystore)
        except BaseException as e:
            raise e

    def export_privkey(self, password):
        '''
        Export privkey for the first receiving address
        :param password: password as string
        :return: private as string

        .. code-block:: python
            testcommond.load_all_wallet()
            testcommond.select_wallet("BTC-1")
            priv = testcommond.export_privkey(password)
            if the wallet you select is "btc" wallet, you will get:
            'cVJo3o48E6j8xxbTQprEzaCWQJ7aL3Y59R2W1owzJnX8NBh5AXnt'

            if the wallet you select is "eth" wallet, you will get:
            '0x31271366888ccad468157770734b5ac0d98a9363d4b229767a28c44fde445f51'
        '''
        try:
            address = self.wallet.get_addresses()[0]
            priv = self.wallet.export_private_key(address, password=password)
            if -1 != priv.find(":"):
                priv = priv.split(":")[1]
            return priv
        except BaseException as e:
            raise e

    def export_seed(self, password, name):
        '''
        Export seed by on-chain wallet
        :param password: password by string
        :return: Mnemonic as string
        '''
        try:
            wallet = self.daemon._wallets[self._wallet_path(name)]
            if not wallet.has_seed():
                raise BaseException(NotSupportExportSeed())
            keystore = wallet.get_keystore()

            seed = keystore.get_seed(password)
            passphrase = keystore.get_passphrase(password)
            return seed + passphrase
        except BaseException as e:
            raise e

    def has_seed(self):
        '''
        Check if the wallet have seed
        :return: True/False as bool
        '''
        if not self.wallet.has_seed():
            raise BaseException(NotSupportExportSeed())
        return self.wallet.has_seed()

    # def check_seed(self, check_seed, password):
    #     try:
    #         self._assert_wallet_isvalid()
    #         if not self.wallet.has_seed():
    #             raise BaseException('This wallet has no seed')
    #         keystore = self.wallet.get_keystore()
    #         seed = keystore.get_seed(password)
    #         if seed != check_seed:
    #             raise BaseException("pair seed failed")
    #         print("pair seed successfule.....")
    #     except BaseException as e:
    #         raise BaseException(e)

    def is_seed(self, x):
        try:
            seed_flag = False
            if keystore.is_seed(x):
                seed_flag = True
            else:
                is_checksum, is_wordlist = keystore.bip39_is_checksum_valid(x)
                if is_checksum:
                    seed_flag = True
            return seed_flag
        except BaseException as e:
            raise e

    def get_addrs_from_seed(self, seed, passphrase=""):
        '''
        Get p2wpkh/p2wpkh-p2sh/p2pkh/electrum address by one seed
        :param seed: seed as str
        :param passphrase: passphrase if you need
        :return:
        '''
        list_type_info = ["p2wpkh", "p2wpkh-p2sh", "p2pkh", "electrum"]
        out = {}
        for type in list_type_info:
            bip39_derivation = ""
            if type == "p2pkh":
                bip39_derivation = bip44_derivation(0, bip43_purpose=44)
            elif type == "p2wpkh":
                bip39_derivation = bip44_derivation(0, bip43_purpose=84)
            elif type == "p2wpkh-p2sh":
                bip39_derivation = bip44_derivation(0, bip43_purpose=49)

            if type == "electrum":
                from electrum.mnemonic import Mnemonic
                bip32_seed = Mnemonic.mnemonic_to_seed(seed, passphrase)
                rootnode = BIP32Node.from_rootseed(bip32_seed, xtype="standard")
                node = rootnode.subkey_at_private_derivation("m/0'/")
            else:
                bip32_seed = keystore.bip39_to_seed(seed, passphrase)
                rootnode = BIP32Node.from_rootseed(bip32_seed, xtype=("standard" if type == "p2pkh" else type))
                node = rootnode.subkey_at_private_derivation(bip39_derivation)

            from electrum import bip32
            xpub_master = bip32.xpub_from_xprv(node.to_xprv())
            node_master = BIP32Node.from_xkey(xpub_master)
            xpub = node_master.subkey_at_public_derivation((0,)).to_xpub()
            node = BIP32Node.from_xkey(xpub).subkey_at_public_derivation((0,))
            pubkey = node.eckey.get_public_key_bytes(compressed=True).hex()
            addr = bitcoin.pubkey_to_address('p2wpkh' if type == 'electrum' else type, pubkey)

            temp = {}
            temp['addr'] = addr
            temp['derivation'] = bip39_derivation
            out[type] = temp
        print(f"out==addr = {out}")
        return json.dumps(out)

    def update_backup_info(self, encrypt_seed):
        '''
        Add wallet backup info to config file
        :param encrypt_seed: encrypt_seed is key as str
        :return:
        '''
        try:
            if not self.backup_info.__contains__(encrypt_seed):
                self.backup_info[encrypt_seed] = False
                self.config.set_key("backupinfo", self.backup_info)
        except BaseException as e:
            raise e

    def get_wallet_by_name(self, name):
        return self.daemon._wallets[self._wallet_path(name)]

    def get_xpub_by_name(self, name, wallet_obj):
        wallet_type = self.get_wallet_type(name)['type']
        if (-1 != wallet_type.find('-hd-') or -1 != wallet_type.find('-derived-')) and -1 == wallet_type.find('-hw-'):
            return self.get_hd_wallet_encode_seed()
        else:
            return wallet_obj.keystore.xpub

    def get_backup_info(self, name):
        '''
        Get backup status
        :param name: Wallet key
        :return: True/False as bool
        '''
        backup_flag = True
        try:
            wallet = self.get_wallet_by_name(name)
            if isinstance(wallet, Imported_Wallet) or isinstance(wallet, Imported_Eth_Wallet):
                return True
            xpub = self.get_xpub_by_name(name, wallet)
            if wallet.has_seed():
                if self.backup_info.__contains__(xpub):
                    backup_flag = False
        except BaseException as e:
            raise e
        return backup_flag

    def delete_backup_info(self, name):
        '''
        Delete one backup status in the config
        :param name: Wallet key
        :return: None
        '''
        try:
            wallet = self.get_wallet_by_name(name)
            xpub = self.get_xpub_by_name(name, wallet)
            if self.backup_info.__contains__(xpub):
                del self.backup_info[xpub]
                self.config.set_key("backupinfo", self.backup_info)
        except BaseException as e:
            raise e

    def create_hd_wallet(self, password, seed=None, passphrase="", purpose=84, strength=128, create_coin=json.dumps(['btc'])):
        '''
        Create hd wallet
        :param password: password as str
        :param seed: import create hd wallet if seed is not None
        :param purpose: 84/44/49 only for btc
        :param strength: Length of the　Mnemonic word as (128/256)
        :param create_coin: List of wallet types to be created like "["btc","eth"]"
        :return: json like {'seed':''
                            'wallet_info':''
                            'derived_info':''}
        '''
        self._assert_daemon_running()
        new_seed = None
        wallet_data = []
        if seed is not None:
            return self.recovery_hd_derived_wallet(password, seed, passphrase)

        if seed is None:
            seed = Mnemonic("english").generate(strength=strength)
            new_seed = seed
            print("Your wallet generation seed is:\n\"%s\"" % seed)
            print("seed type = %s" % type(seed))

        ##create BTC-1
        create_coin_list = json.loads(create_coin)
        if "btc" in create_coin_list:
            wallet_info = self.create("BTC-1", password, seed=seed, passphrase=passphrase,
                                  bip39_derivation=bip44_derivation(0, purpose),
                                  hd=True)
            wallet_data.append(json.loads(wallet_info))

        for coin, info in self.coins.items():
            if coin in create_coin_list:
                name = "%s-1" % coin.upper()
                bip39_derivation = bip44_derivation(0, info['addressType'], info['coinId'])
                wallet_info = self.create(name, password, seed=seed, passphrase=passphrase, bip39_derivation=bip39_derivation, hd=True, coin=coin)
                wallet_data.append(json.loads(wallet_info))
        key = self.get_hd_wallet_encode_seed(seed=seed)
        self.update_backup_info(key)

        out_info = []
        for info in wallet_data:
            out_info.append(info["wallet_info"][0])
        out = self.get_create_info_by_json(new_seed, out_info)
        return json.dumps(out)

    def get_wallet_num(self):
        list_info = json.loads(self.list_wallets())
        num = 0
        name = {}
        for info in list_info:
            for key, value in info.items():
                if -1 == value['type'].find("-hw-"):
                    num += 1
        return num

    def get_temp_file(self):
        return ''.join(random.sample(string.ascii_letters + string.digits, 8)) + '.unique.file'

    def get_unique_path(self, wallet_info):
        import hashlib
        return hashlib.sha256(wallet_info.get_addresses()[0].encode()).hexdigest()

    def verify_legality(self, data, flag='', coin='btc', password=None):
        '''
        Verify legality for seed/private/public/address
        :param data: data as string
        :param falg: seed/private/public/address/keystore as string
        :param coin: btc/eth as string
        :return: raise except if failed
        '''
        if flag == "seed":
            is_checksum, is_wordlist = keystore.bip39_is_checksum_valid(data)
            if not keystore.is_seed(data) and not is_checksum:
                raise BaseException(_("Incorrect mnemonic format."))
        if coin == "btc":
            if flag == "private":
                try:
                    ecc.ECPrivkey(bfh(data))
                except:
                    private_key = keystore.get_private_keys(data, allow_spaces_inside_key=False)
                    if private_key is None:
                        raise BaseException(UnavailablePrivateKey())
            elif flag == "public":
                try:
                    ecc.ECPubkey(bfh(data))
                except:
                    raise BaseException(UnavailablePublicKey())
            elif flag == "address":
                if not bitcoin.is_address(data):
                    raise BaseException(UnavailableBtcAddr())
        elif self.coins.__contains__(coin):
            if flag == "private":
                try:
                    keys.PrivateKey(HexBytes(data))
                except:
                    raise BaseException(UnavailablePrivateKey())
            elif flag == "keystore":
                try:
                    Account.decrypt(json.loads(data), password).hex()
                except BaseException as e:
                    raise BaseException(_("Incorrect eth keystore."))
            elif flag == "public":
                try:
                    uncom_key = get_uncompressed_key(data)
                    keys.PublicKey(HexBytes(uncom_key[2:]))
                except:
                    raise BaseException(UnavailablePublicKey())
            elif flag == "address":
                if not self.pywalib.web3.isAddress(data):
                    raise BaseException(UnavailableEthAddr())

    def replace_watch_only_wallet(self, replace=True):
        '''
        When a watch-only wallet exists locally and a non-watch-only wallet is created,
        the interface can be called to delete the watch-only wallet and keey the non-watch-only wallet
        :param replace:True/False as bool
        :return: wallet key as string
        '''
        if replace:
            self.delete_wallet(password=self.replace_wallet_info['password'], name=self.replace_wallet_info['old_key'])
            self.create_new_wallet_update(wallet=self.replace_wallet_info['wallet'],
                                          name=self.replace_wallet_info['name'],
                                          seed=self.replace_wallet_info['seed'],
                                          password=self.replace_wallet_info['password'],
                                          coin=self.replace_wallet_info['coin'],
                                          wallet_type=self.replace_wallet_info['wallet_type'],
                                          derived_flag=self.replace_wallet_info['derived_flag'],

                                        bip39_derivation=self.replace_wallet_info['bip39_derivation'])
        wallet=self.replace_wallet_info['wallet']
        self.replace_wallet_info = {}
        return str(wallet)

    def update_replace_info(self, old_key, new_wallet, name=None, seed=None, \
                            password=None, coin='btc', wallet_type=None, derived_flag=None, bip39_derivation=None):
        self.replace_wallet_info['old_key'] = old_key
        self.replace_wallet_info['wallet'] = new_wallet
        self.replace_wallet_info['name'] = name
        self.replace_wallet_info['seed'] = seed
        self.replace_wallet_info['password'] = password
        self.replace_wallet_info['coin'] = coin
        self.replace_wallet_info['wallet_type'] = wallet_type
        self.replace_wallet_info['derived_flag'] = derived_flag
        self.replace_wallet_info['bip39_derivation'] = bip39_derivation

    def create_new_wallet_update(self, wallet=None, name=None, seed=None,\
                                     password=None, coin='btc', wallet_type=None, derived_flag=False, \
                                     bip39_derivation=None):
        try:
            wallet.set_name(name)
            wallet.save_db()
            wallet.update_password(old_pw=None, new_pw=password, str_pw=self.android_id, encrypt_storage=True)
            self.daemon.add_wallet(wallet)
            if coin == "btc":
                wallet.start_network(self.daemon.network)
            self.update_local_wallet_info(self.get_unique_path(wallet), wallet_type)
            if derived_flag:
                self.set_hd_wallet(wallet)
                self.update_devired_wallet_info(bip39_derivation, self.get_hd_wallet_encode_seed(seed=seed, coin=coin),
                                                name)
        except BaseException as e:
            raise e

    def create(self, name, password=None, seed_type="segwit", seed=None, passphrase="", bip39_derivation=None,
               master=None, addresses=None, privkeys=None, hd=False, purpose=49, coin="btc", keystores=None, keystore_password=None,
               strength=128):
        '''
        Create or restore a new wallet
        :param name: Wallet name as string
        :param password: Password ans string
        :param seed_type: Not for now
        :param seed: Mnemonic word as string
        :param passphrase:Customised passwords as string
        :param bip39_derivation:Not for now
        :param master:Not for now
        :param addresses:To create a watch-only wallet you need
        :param privkeys:To create a wallet with a private key you need
        :param hd:Not for app
        :param purpose:BTC address type as (44/49/84), for BTC only
        :param coin:"btc"/"eth" as string to specify whether to create a BTC/ETH wallet
        :param keystores:as string for ETH only
        :param strength:Length of the　Mnemonic word as (128/256)
        :return: json like {'seed':''
                            'wallet_info':''
                            'derived_info':''}
        .. code-block:: python
                create a btc wallet by address:
                    create("test5", addresses="bcrt1qzm6y9j0zg9nnludkgtc0pvhet0sf76szjw7fjw")
                create a eth wallet by address:
                    create("test4", addresses="0x....", coin="eth")

                create a btc wallet by privkey:
                    create("test3", password=password, purpose=84, privkeys="cRR5YkkGHTph8RsM1bQv7YSzY27hxBBhoJnVdHjGnuKntY7RgoGw")
                create a eth wallet by privkey:
                    create("test3", password=password, privkeys="0xe6841ceb170becade0a4aa3e157f08871192f9de1c35835de5e1b47fc167d27e", coin="eth")

                create a btc wallet by seed:
                    create(name, password, seed='pottery curtain belt canal cart include raise receive sponsor vote embody offer')
                create a eth wallet by seed:
                    create(name, password, seed='pottery curtain belt canal cart include raise receive sponsor vote embody offer', coin="eth")

        '''
        print("CREATE in....name = %s" % name)
        try:
            if self.get_wallet_num() == 0:
                self.check_pw_wallet = None
            if addresses is None:
                self.check_password(password)
        except BaseException as e:
            raise e

        new_seed = ""
        derived_flag = False
        wallet_type = "%s-standard" % coin
        temp_path = self.get_temp_file()
        path = self._wallet_path(temp_path)
        if exists(path):
            raise BaseException(FileAlreadyExist())
        storage = WalletStorage(path)
        db = WalletDB('', manual_upgrades=False)
        if addresses is not None:
            if coin == "btc":
                wallet = Imported_Wallet(db, storage, config=self.config)
                addresses = addresses.split()
                if not bitcoin.is_address(addresses[0]):
                    try:
                        pubkey = ecc.ECPubkey(bfh(addresses[0])).get_public_key_hex()
                        addresses = [bitcoin.pubkey_to_address("p2wpkh", pubkey)]
                    except BaseException as e:
                        raise BaseException(_("Incorrect address or pubkey."))
            elif coin == "eth" or coin == "bsc":
                wallet = Imported_Eth_Wallet(db, storage, config=self.config)
                wallet.wallet_type = '%s_imported' % coin
                try:
                    from eth_keys import keys
                    from hexbytes import HexBytes
                    pubkey = keys.PublicKey(HexBytes(addresses))
                    addresses = pubkey.to_address()
                except BaseException as e:
                    addresses = addresses.split()
            else:
                raise BaseException("Only support BTC/ETH")
            good_inputs, bad_inputs = wallet.import_addresses(addresses, write_to_disk=False)
            # FIXME tell user about bad_inputs
            if not good_inputs:
                raise BaseException(_("No address available."))
            wallet_type = "%s-watch-standard" % coin
        elif privkeys is not None or keystores is not None:
            if coin == "btc":
                ks = keystore.Imported_KeyStore({})
                db.put('keystore', ks.dump())
                wallet = Imported_Wallet(db, storage, config=self.config)
                try:
                    # TODO:need script(p2pkh/p2wpkh/p2wpkh-p2sh)
                    if privkeys[0:2] == '0x':
                        privkeys = privkeys[2:]
                    ecc.ECPrivkey(bfh(privkeys))
                    keys = [privkeys]
                except BaseException as e:
                    addr_type = 'p2pkh' if purpose == 44 else 'p2wpkh' if purpose == 84 else 'p2wpkh-p2sh' if purpose == 49 else ""
                    if addr_type != "":
                        privkeys = '%s:%s' % (addr_type, privkeys)
                    keys = keystore.get_private_keys(privkeys, allow_spaces_inside_key=False)
                    if keys is None:
                        raise BaseException(UnavailablePrivateKey())
            elif coin == "eth" or coin == "bsc":
                if keystores is not None:
                    try:
                        from eth_account import Account
                        privkeys = Account.decrypt(keystores, keystore_password).hex()
                    except ValueError:
                        raise BaseException(str(InvalidPassword()))
                k = keystore.Imported_KeyStore({})
                db.put('keystore', k.dump())
                wallet = Imported_Eth_Wallet(db, storage, config=self.config)
                wallet.wallet_type = '%s_imported' % coin
                keys = keystore.get_eth_private_key(privkeys, allow_spaces_inside_key=False)
            good_inputs, bad_inputs = wallet.import_private_keys(keys, None, write_to_disk=False)
            # FIXME tell user about bad_inputs
            if not good_inputs:
                raise BaseException(_("No private key available."))
            wallet_type = "%s-private-standard" % coin
        else:
            if bip39_derivation is not None:
                if keystore.is_seed(seed):
                    ks = keystore.from_seed(seed, passphrase, False)
                else:
                    is_checksum, is_wordlist = keystore.bip39_is_checksum_valid(seed)
                    if not is_checksum:
                        raise BaseException(InvalidBip39Seed())
                    ks = keystore.from_bip39_seed(seed, passphrase, bip39_derivation)
                #if int(bip39_derivation.split('/')[ACCOUNT_POS].split('\'')[0]) != 0:
                wallet_type = '%s-derived-standard' % coin
                derived_flag = True

            elif master is not None:
                ks = keystore.from_master_key(master)
            else:
                if seed is None:
                    seed = Mnemonic("english").generate(strength=strength)
                    # seed = mnemonic.Mnemonic('en').make_seed(seed_type=seed_type)
                    new_seed = seed
                    print("Your wallet generation seed is:\n\"%s\"" % seed)
                    print("seed type = %s" % type(seed))
                if keystore.is_seed(seed):
                    ks = keystore.from_seed(seed, passphrase, False)
                else:
                    is_checksum, is_wordlist = keystore.bip39_is_checksum_valid(seed)
                    if not is_checksum:
                        raise BaseException(InvalidBip39Seed())
                    if coin == "btc":
                        derivation = bip44_derivation(0, purpose)
                    elif self.coins.__contains__(coin):
                        derivation = bip44_derivation(0, self.coins[coin]['addressType'],
                                                      coin=self.coins[coin]['coinId'])
                    ks = keystore.from_bip39_seed(seed, passphrase, derivation=derivation)
            db.put('keystore', ks.dump())
            if coin == "btc":
                wallet = Standard_Wallet(db, storage, config=self.config)
            elif self.coins.__contains__(coin):
                wallet = Standard_Eth_Wallet(db, storage, config=self.config)
        addr = wallet.get_addresses()[0]
        print(f"addre........{addr, bip39_derivation}")
        new_name = self.get_unique_path(wallet)
        wallet.storage.set_path(self._wallet_path(new_name))
        try:
            self.check_exist_file(wallet)
        except ReplaceWatchonlyWallet:
            self.update_replace_info(str(ReplaceWatchonlyWallet.message), wallet, name=name, seed=seed,\
                                     password=password, coin=coin, wallet_type=wallet_type, derived_flag=derived_flag, \
                                     bip39_derivation=bip39_derivation)
            raise BaseException("Replace Watch-olny wallet:%s" %new_name)
        except BaseException as e:
            raise e
        try:
            self.create_new_wallet_update(name=name, seed=seed, password=password, wallet=wallet, coin=coin, \
                                          wallet_type=wallet_type, derived_flag=derived_flag, bip39_derivation=bip39_derivation)
        except BaseException as e:
            raise e
        if new_seed != '':
            key = wallet.keystore.xpub
            print(f"....create........{key}")
            self.update_backup_info(key)
        wallet_info = CreateWalletInfo.create_wallet_info(coin_type=coin, name=wallet.__str__())
        out = self.get_create_info_by_json(new_seed, wallet_info)
        return json.dumps(out)

    def get_create_info_by_json(self, seed='', wallet_info=None, derived_info=None):
        from electrum_gui.android.create_wallet_info import CreateWalletInfo
        create_wallet_info = CreateWalletInfo()
        create_wallet_info.add_seed(seed)
        create_wallet_info.add_wallet_info(wallet_info)
        create_wallet_info.add_derived_info(derived_info)
        return create_wallet_info.to_json()

    def is_watch_only(self):
        '''
        Check if it is watch only wallet
        :return: True/False as bool
        '''
        self._assert_wallet_isvalid()
        return self.wallet.is_watching_only()

    def load_all_wallet(self):
        '''
        Load all wallet info
        :return:None
        '''
        name_wallets = sorted([name for name in os.listdir(self._wallet_path())])
        name_info = {}
        for name in name_wallets:
            self.load_wallet(name, password=self.android_id)

    def update_wallet_password(self, old_password, new_password):
        '''
        Update password
        :param old_password: old_password as string
        :param new_password: new_password as string
        :return:None
        '''
        self._assert_daemon_running()
        for name, wallet in self.daemon._wallets.items():
            wallet.update_password(old_pw=old_password, new_pw=new_password, str_pw=self.android_id)

    def check_password(self, password):
        '''
        Check wallet password
        :param password: as string
        :return: raise except if error
        '''
        try:
            if self.check_pw_wallet is None:
                self.check_pw_wallet = self.get_check_wallet()
            self.check_pw_wallet.check_password(password, str_pw=self.android_id)
        except BaseException as e:
            if len(e.args) != 0:
                if -1 != e.args[0].find("out of range"):
                    pass
            else:
                raise e

    def recovery_confirmed(self, name_list, hw=False):
        '''
        If you import hd wallet by seed, you will get a name list
        and you need confirm which wallets you want to import
        :param name_list: wallets you want to import as list like [name, name2,...]
        :param hw: True if you recovery from hardware
        :return:None
        '''
        name_list = json.loads(name_list)
        if len(name_list) != 0:
            for name in name_list:
                if self.recovery_wallets.__contains__(name):
                    recovery_info = self.recovery_wallets.get(name)
                    wallet = recovery_info['wallet']
                    self.daemon.add_wallet(wallet)
                    wallet.hide_type = False
                    # wallet.set_name(recovery_info['name'])
                    wallet.save_db()
                    ##set self.hd_wallet if name == 'BTC-1' and account_id == 0
                    #if self.is_have_hd_wallet(wallet_type):
                    if not hw:
                        self.set_hd_wallet(wallet)
                    #self.update_wallet_name(wallet.__str__(), self.get_unique_path(wallet))
                    coin = wallet.wallet_type[0:3]
                    if self.coins.__contains__(coin):
                        # if not hw:
                        #     self.coins[coin]['derivedInfoObj'].update_recovery_info(recovery_info['account_id'])
                        wallet_type = '%s-hw-derived-%s-%s' % (coin, wallet.m, wallet.n) if hw else (
                                '%s-derived-standard' % coin)
                        self.update_devired_wallet_info(
                            bip44_derivation(recovery_info['account_id'], bip43_purpose=self.coins[coin]['addressType'],
                                             coin=self.coins[coin]['coinId']),
                            recovery_info['key'], wallet.get_name())
                    else:
                        # if not hw:
                        #     self.btc_derived_info.update_recovery_info(recovery_info['account_id'])
                        wallet_type = 'btc-hw-derived-%s-%s' % (1, 1) if hw else (
                            'btc-derived-standard')
                        self.update_devired_wallet_info(bip44_derivation(recovery_info['account_id'], bip43_purpose=84,
                                                                         coin=constants.net.BIP44_COIN_TYPE),
                                                        recovery_info['key'], wallet.get_name())
                    self.update_local_wallet_info(self.get_unique_path(wallet), wallet_type)
        self.recovery_wallets.clear()

    def delete_derived_wallet(self):
        wallet = json.loads(self.list_wallets())
        for wallet_info in wallet:
            for key, info in wallet_info.items():
                try:
                    if -1 != info['type'].find('-derived-') and -1 == info['type'].find('-hw-'):
                        wallet_obj = self.daemon._wallets[self._wallet_path(key)]
                        self.delete_wallet_devired_info(wallet_obj)
                        self.delete_wallet_from_deamon(self._wallet_path(key))
                        if self.local_wallet_info.__contains__(key):
                            self.local_wallet_info.pop(key)
                            self.config.set_key('all_wallet_type_info', self.local_wallet_info)
                except Exception as e:
                    raise BaseException(e)
        self.hd_wallet = None

    def get_check_wallet(self):
        wallets = self.daemon.get_wallets()
        for key, wallet in wallets.items():
            if not isinstance(wallet.keystore, Hardware_KeyStore) and not wallet.is_watching_only():
                return wallet
                # key = sorted(wallets.keys())[0]
                # # value = wallets.values()[0]
                # return wallets[key]

    def update_recover_list(self, recovery_list, balance, wallet_obj, label, coin):
        show_info = {}
        show_info['coin'] = coin
        show_info['blance'] = str(balance)
        show_info['name'] = str(wallet_obj)
        show_info['label'] = label
        try:
            self.check_exist_file(wallet_obj)
            show_info['exist'] = "0"
        except:
            show_info['exist'] = "1"
        recovery_list.append(show_info)

    def filter_wallet(self):
        recovery_list = []
        for name, wallet_info in self.recovery_wallets.items():
            try:
                wallet = wallet_info['wallet']
                coin = wallet.wallet_type[0:3]
                if self.coins.__contains__(wallet.wallet_type[0:3]):
                    address = wallet.get_addresses()[0]
                    tx_list = self.get_eth_tx_list(wallet, address)
                    if len(tx_list) != 0:
                        self.update_recover_list(recovery_list, wallet.get_all_balance(address, coin), wallet,
                                                 wallet.get_name(), coin)
                        continue
                else:
                    addr = wallet.get_addresses()[0]
                    history = reversed(wallet.get_history())
                    for item in history:
                        c, u, _ = wallet.get_balance()
                        self.update_recover_list(recovery_list, self.format_amount_and_units(c + u), wallet,
                                                 wallet.get_name(), "btc")
                        break
            except BaseException as e:
                raise e
        return recovery_list

    def update_recovery_wallet(self, key, wallet_obj, bip39_derivation, name):
        wallet_info = {}
        wallet_info['key'] = key
        wallet_info['wallet'] = wallet_obj
        wallet_info['account_id'] = int(bip39_derivation.split('/')[ACCOUNT_POS].split('\'')[0])
        wallet_info['name'] = name
        return wallet_info

    def get_coin_derived_path(self, account_id, coin='btc', purpose=84):
        if coin == "btc":
            return bip44_derivation(account_id, bip43_purpose=purpose)
        else:
            return bip44_derivation(account_id, self.coins[coin]['addressType'], coin=self.coins[coin]['coinId'])

    def recovery_import_create_hw_wallet(self, i, name, m, n, xpubs, coin='btc'):
        try:
            if coin == 'btc':
                print(f"xpubs=========={m, n, xpubs}")
                self.set_multi_wallet_info(name, m, n)
                self.add_xpub(xpubs, self.hw_info['device_id'], i, self.hw_info['type'])

                temp_path = self.get_temp_file()
                path = self._wallet_path(temp_path)
                keystores = self.get_keystores_info()
                # if not hide_type:
                #     for key, value in self.local_wallet_info.items():
                #         num = 0
                #         for xpub in keystores:
                #             if value['xpubs'].__contains__(xpub):
                #                 num += 1
                #         if num == len(keystores) and value['type'] == wallet_type:
                #             raise BaseException(f"The same xpubs have create wallet, name={key}")
                storage, db = self.wizard.create_storage(path=path, password='', hide_type=True)
                if storage:
                    wallet = Wallet(db, storage, config=self.config)
                    # wallet.status_flag = ("btc-hw-%s-%s" % (m, n))
                    wallet.set_name(name)
                    wallet.hide_type = True
                    new_name = self.get_unique_path(wallet)
                    wallet.storage.set_path(self._wallet_path(new_name))
                    wallet.update_password(old_pw=None, new_pw=None, str_pw=self.android_id, encrypt_storage=True)
                    wallet.start_network(self.daemon.network)
                    self.recovery_wallets[new_name] = self.update_recovery_wallet(xpubs, wallet,
                                                                                  self.get_coin_derived_path(i, coin),
                                                                                  name)
                    self.wizard = None
            else:
                print("TODO recovery create from hw")
        except Exception as e:
            raise BaseException(e)

    def get_type(self, xtype):
        return 'p2wpkh-p2sh' if xtype == 49 else 'p2pkh' if xtype == 44 else 'p2wpkh' if xtype == 84 else ""

    def recovery_create_subfun(self, coin, account_id, hw, name, seed, password, passphrase, path):
        try:
            if coin == 'btc':
                type_list = [49, 44, 84]
            elif self.coins.__contains__(coin):
                type_list = [self.coins[coin]['addressType']]

            for xtype in type_list:
                bip39_derivation = self.get_coin_derived_path(account_id, coin, purpose=xtype)
                if not AndroidCommands._recovery_flag:
                    self.recovery_wallets.clear()
                    AndroidCommands._recovery_flag = True
                    raise UserCancel()
                if not hw:
                    self.recovery_create(name, seed, password=password,
                                         bip39_derivation=bip39_derivation,
                                         passphrase=passphrase, coin=coin)
                else:
                    xpub = self.get_xpub_from_hw(path=path, _type=self.get_type(xtype), account_id=account_id,
                                                 coin=coin)
                    self.recovery_import_create_hw_wallet(account_id, name, 1, 1, xpub, coin=coin)
            return True
        except BaseException as e:
            AndroidCommands._recovery_flag = True
            raise e

    def recovery_wallet(self, seed=None, password=None, passphrase="", coin='btc', xpub=None, hw=False,
                        path='bluetooth'):
        derived = DerivedInfo(self.config)
        derived.init_recovery_num()
        type_list = [49, 44, 84]
        for derived_xpub, derived_wallet in self.derived_info.items():
            if xpub == derived_xpub:
                for derived_info in derived_wallet:
                    flag = self.recovery_create_subfun(coin, derived_info['account_id'], hw, derived_info['name'], seed,
                                                       password, passphrase, path)
                    if not flag:
                        return False
                    derived.update_recovery_info(derived_info['account_id'])
        derived.reset_list()
        account_list = derived.get_list()
        ##create the wallet 0~19 that not in the config file, the wallet must have tx history
        if account_list is not None:
            for i in account_list:
                flag = self.recovery_create_subfun(coin, i, hw, "%s_derived_%s" % (coin, i), seed, password, passphrase,
                                                   path)
                if not flag:
                    return False

    def get_hd_wallet(self):
        if self.hd_wallet is None:
            wallets = self.daemon.get_wallets()
            for key, wallet in wallets.items():
                if self.local_wallet_info.__contains__(self.get_unique_path(wallet)):
                    wallet_info = self.local_wallet_info.get(self.get_unique_path(wallet))
                    wallet_type = wallet_info['type']
                    if self.is_have_hd_wallet(wallet_type):
                        self.hd_wallet = wallet
                        return wallet
            raise BaseException(UnavaiableHdWallet())
        else:
            return self.hd_wallet

    def get_hd_wallet_encode_seed(self, seed=None, coin='', passphrase=''):
        if seed is not None:
            path = bip44_derivation(0, 84)
            ks = keystore.from_bip39_seed(seed, passphrase, path)
            self.config.set_key('current_hd_xpub', ks.xpub)
            return ks.xpub + coin.lower()
        else:
            return self.config.get("current_hd_xpub", "") + coin.lower()

    @classmethod
    def _set_recovery_flag(cls, flag=False):
        '''
        Support recovery process was withdrawn
        :param flag: true/false
        :return:
        '''
        cls._recovery_flag = flag

    def filter_wallet_with_account_is_zero(self):
        wallet_list = []
        for name, wallet_info in self.recovery_wallets.items():
            try:
                wallet = wallet_info['wallet']
                coin = wallet.wallet_type[0:3]
                derivation = wallet.keystore.get_derivation_prefix()
                account_id = int(derivation.split('/')[ACCOUNT_POS].split('\'')[0])
                purpose = int(derivation.split('/')[PURPOSE_POS].split('\'')[0])
                if account_id != 0:
                    continue
                if self.coins.__contains__(coin) or purpose == 49:
                    wallet_info = CreateWalletInfo.create_wallet_info(coin_type="btc" if purpose == 49 else coin, name=str(wallet))
                    wallet_list.append(wallet_info[0])
            except BaseException as e:
                raise e
        return wallet_list

    def recovery_hd_derived_wallet(self, password=None, seed=None, passphrase='', xpub=None, hw=False,
                                   path='bluetooth'):
        if hw:
            self.recovery_wallet(xpub=xpub, hw=hw, path=path)
        else:
            xpub = self.get_hd_wallet_encode_seed(seed=seed, coin='btc')
            self.recovery_wallet(seed, password, passphrase, xpub=xpub, hw=hw)

            if 'ANDROID_DATA' in os.environ:
                for coin, info in self.coins.items():
                    xpub = self.get_hd_wallet_encode_seed(seed=seed, coin=coin)
                    PyWalib.set_server(info)
                    self.recovery_wallet(seed, password, passphrase, coin=coin, xpub=xpub, hw=hw)

        recovery_list = self.filter_wallet()
        wallet_data = self.filter_wallet_with_account_is_zero()
        out = self.get_create_info_by_json(wallet_info=wallet_data, derived_info=recovery_list)
        return json.dumps(out)

    def get_derivat_path(self, purpose=84, coin=None):
        '''
        Get derivation path
        :param coin_type: 44/49/84 as int
        :return: 'm/44'/0/0' as string
        '''
        if coin != 'btc':
            purpose = self.coins[coin]['addressType']
            coin = self.coins[coin]['coinId']
        return bip44_derivation(0, purpose, coin=coin)

    def create_derived_wallet(self, name, password, coin='btc', purpose=84, strength=128):
        '''
        Create BTC/ETH derived wallet
        :param name: name as str
        :param password: password as str
        :param coin: btc/eth as str
        :param purpose: (44/84/49) for btc only
        :param strength: Length of the　Mnemonic word as (128/256)
        :return: json like {'seed':''
                            'wallet_info':''
                            'derived_info':''}
        '''

        self._assert_coin_isvalid(coin)
        try:
            self.check_password(password)
        except BaseException as e:
            raise e

        # derived_info = self.btc_derived_info if coin == 'btc' else self.coins[coin]['derivedInfoObj']
        seed = self.get_hd_wallet().get_seed(password)
        coin_type = constants.net.BIP44_COIN_TYPE if coin == 'btc' else self.coins[coin]['coinId']
        purpose = purpose if coin == 'btc' else self.coins[coin]['addressType']
        encode_seed = self.get_hd_wallet_encode_seed(seed=seed, coin=coin)
        list_info = self.get_derived_list(encode_seed)
        # list_info = derived_info.get_list()

        if list_info is not None:
            if len(list_info) == 0:
                from electrum.i18n import _
                raise BaseException(DerivedWalletLimit())
            account_id = list_info[0]
        else:
            account_id = 0
        # hd_flag = True
        # wallet_type = '%s-hd-standard' % coin
        # for wallet_info in json.loads(self.list_wallets()):
        #     for _, info in wallet_info.items():
        #         if info['type'] == wallet_type:
        #             hd_flag = False

        derivat_path = bip44_derivation(account_id, purpose, coin=coin_type)
        return self.create(name, password, seed=seed, bip39_derivation=derivat_path, strength=strength,
                           coin=coin)

    def recovery_create(self, name, seed, password, bip39_derivation, passphrase='', coin="btc"):
        try:
            self.check_password(password)
        except BaseException as e:
            raise e
        if int(bip39_derivation.split('/')[ACCOUNT_POS].split('\'')[0]) == 0:
            purpose = int(bip39_derivation.split('/')[PURPOSE_POS].split('\'')[0])
            if purpose == 49:
                name = "BTC-1"
            elif self.coins.__contains__(coin):
                name = "%s-1" %coin.upper()
            else:
                name = "btc-derived-%s" %purpose
        temp_path = self.get_temp_file()
        path = self._wallet_path(temp_path)
        if exists(path):
            raise BaseException(FileAlreadyExist())
        storage = WalletStorage(path)
        db = WalletDB('', manual_upgrades=False)
        ks = keystore.from_bip39_seed(seed, passphrase, bip39_derivation)
        db.put('keystore', ks.dump())
        if self.coins.__contains__(coin):
            print("")
            wallet = Standard_Eth_Wallet(db, storage, config=self.config)
            wallet.wallet_type = '%s_standard' % coin
        else:
            wallet = Standard_Wallet(db, storage, config=self.config)
        wallet.hide_type = True
        wallet.set_name(name)
        new_name = self.get_unique_path(wallet)
        wallet.storage.set_path(self._wallet_path(new_name))
        self.recovery_wallets[new_name] = self.update_recovery_wallet(self.get_hd_wallet_encode_seed(seed=seed, coin=coin), wallet, bip39_derivation, name)
        wallet.update_password(old_pw=None, new_pw=password, str_pw=self.android_id, encrypt_storage=True)
        if coin == 'btc':
            wallet.start_network(self.daemon.network)
        wallet.save_db()

    def get_devired_num(self, coin='btc'):
        '''
        Get devired HD num by app
        :param coin:btc/eth as string
        :return: num as int
        '''
        xpub = self.get_hd_wallet_encode_seed(coin=coin.lower())
        num = 0
        if self.derived_info.__contains__(xpub):
            num = len(self.derived_info[xpub])
        return num

    def delete_devired_wallet_info(self, wallet_obj):
        derivation = wallet_obj.keystore.get_derivation_prefix()
        account_id = int(derivation.split('/')[ACCOUNT_POS].split('\'')[0])
        if self.coins.__contains__(wallet_obj.wallet_type[0:3]):
            coin = wallet_obj.wallet_type[0:3]
        else:
            coin = 'btc'
        xpub = self.get_hd_wallet_encode_seed(coin=coin)
        if self.derived_info.__contains__(xpub):
            derived_wallet = self.derived_info[xpub]
            for pos, wallet_info in enumerate(derived_wallet):
                if int(wallet_info['account_id']) == account_id:
                    derived_wallet.pop(pos)
                    break
            self.derived_info[xpub] = derived_wallet
            self.config.set_key("derived_info", self.derived_info)

    def update_devired_wallet_info(self, bip39_derivation, xpub, name):
        wallet_info = {}
        wallet_info['name'] = name
        wallet_info['account_id'] = bip39_derivation.split('/')[ACCOUNT_POS][:-1]
        if not self.derived_info.__contains__(xpub):
            derivated_wallets = []
            derivated_wallets.append(wallet_info)
            self.derived_info[xpub] = derivated_wallets
            self.config.set_key("derived_info", self.derived_info)
        else:
            save_flag = False
            from .derived_info import RECOVERY_DERIVAT_NUM
            if len(self.derived_info[xpub]) <= RECOVERY_DERIVAT_NUM:
                derived_wallet = self.derived_info[xpub]
                for info in derived_wallet:
                    if info['account_id'] == wallet_info['account_id']:
                        save_flag = True
                if not save_flag:
                    self.derived_info[xpub].append(wallet_info)
                    self.config.set_key("derived_info", self.derived_info)

    def get_all_mnemonic(self):
        '''
        Get all mnemonic, num is 2048
        :return: json like "[job, ...]"
        '''
        return json.dumps(Wordlist.from_file('english.txt'))

    def get_all_wallet_balance(self):
        '''
        Get all wallet balances
        :return:
        {
          "all_balance": "21,233.46 CNY",
          "wallet_info": [
            {
              "name": "",
              "btc": "0.005 BTC",
              "fiat": "1,333.55",
              "wallets": [
                {"coin": "btc", "balance": "", "fiat": ""}
              ]
            },
            {
              "name": "",
              "wallets": [
                { "coin": "btc", "balance": "", "fiat": ""},
                { "coin": "usdt", "balance": "", "fiat": ""}
              ]
            }
          ]
        }
        '''
        out = {}
        try:
            all_balance = Decimal('0')
            all_wallet_info = []
            for wallet in self.daemon.get_wallets().values():
                wallet_info = {'name': wallet.get_name()}
                if self.coins.__contains__(wallet.wallet_type[0:3]):
                    checksum_from_address = self.pywalib.web3.toChecksumAddress(wallet.get_addresses()[0])
                    balances_info = wallet.get_all_balance(checksum_from_address,
                                                           self.coins[wallet.wallet_type[0:3]]['symbol'])
                    wallet_balances = []
                    for symbol, info in balances_info.items():
                        copied_info = dict(info)
                        copied_info['fiat'] = self.daemon.fx.format_amount_and_units(copied_info['fiat'] * COIN) or f"0 {self.ccy}"
                        wallet_balances.append({
                            "coin": symbol,
                            **copied_info,
                        })
                        fiat = Decimal(copied_info['fiat'].split()[0].replace(',', ""))
                        all_balance += fiat

                    wallet_info['wallets'] = wallet_balances
                    all_wallet_info.append(wallet_info)
                else:
                    c, u, x = wallet.get_balance()
                    balance = self.daemon.fx.format_amount_and_units(c + u) or f"0 {self.ccy}"
                    fiat = Decimal(balance.split()[0].replace(',', ""))
                    all_balance += fiat
                    wallet_info['btc'] = self.format_amount(c + u)  # fixme deprecated field
                    wallet_info['fiat'] = balance  # fixme deprecated field
                    wallet_info['wallets'] = [{"coin": "btc", "balance": wallet_info['btc'], "fiat": wallet_info['fiat']}]
                    all_wallet_info.append(wallet_info)

            out['all_balance'] = ('%s %s' % (all_balance, self.ccy))
            out['wallet_info'] = all_wallet_info
            return json.dumps(out, cls=DecimalEncoder)
        except BaseException as e:
            raise e

    def check_exist_file(self, wallet_obj):
        for _, exist_wallet in self.daemon._wallets.items():
            if wallet_obj.get_addresses()[0] in exist_wallet.get_addresses()[0]:
                if exist_wallet.is_watching_only() and not wallet_obj.is_watching_only():
                    raise ReplaceWatchonlyWallet(exist_wallet)
                else:
                    raise BaseException(FileAlreadyExist())
    #####
    # rbf api
    def set_rbf(self, status_rbf):
        '''
        Enable/disable rbf
        :param status_rbf:True/False as bool
        :return:
        '''
        use_rbf = self.config.get('use_rbf', True)
        if use_rbf == status_rbf:
            return
        self.config.set_key('use_rbf', status_rbf)
        self.rbf = status_rbf

    def get_rbf_status(self, tx_hash):
        try:
            tx = self.wallet.db.get_transaction(tx_hash)
            if not tx:
                return False
            height = self.wallet.get_tx_height(tx_hash).height
            _, is_mine, v, fee = self.wallet.get_wallet_delta(tx)
            is_unconfirmed = height <= 0
            if tx:
                # note: the current implementation of RBF *needs* the old tx fee
                rbf = is_mine and self.rbf and fee is not None and is_unconfirmed
                if rbf:
                    return True
                else:
                    return False
        except BaseException as e:
            raise e

    def format_fee_rate(self, fee_rate):
        # fee_rate is in sat/kB
        return util.format_fee_satoshis(fee_rate / 1000, num_zeros=self.num_zeros) + ' sat/byte'

    def get_rbf_fee_info(self, tx_hash):
        tx = self.wallet.db.get_transaction(tx_hash)
        if not tx:
            raise BaseException(FailedGetTx())
        txid = tx.txid()
        assert txid
        fee = self.wallet.get_tx_fee(txid)
        if fee is None:
            raise BaseException(
                _("RBF(Replace-By-Fee) fails because it does not get the fee intormation of the original transaction."))
        tx_size = tx.estimated_size()
        old_fee_rate = fee / tx_size  # sat/vbyte
        new_rate = Decimal(max(old_fee_rate * 1.5, old_fee_rate + 1)).quantize(Decimal('0.0'))
        new_tx = json.loads(self.create_bump_fee(tx_hash, str(new_rate)))
        ret_data = {
            'current_feerate': self.format_fee_rate(1000 * old_fee_rate),
            'new_feerate': str(new_rate),
            'fee': new_tx['fee'],
            'tx': new_tx['new_tx']
        }
        return json.dumps(ret_data)

    # TODO:new_tx in history or invoices, need test

    def create_bump_fee(self, tx_hash, new_fee_rate):
        try:
            print("create bump fee tx_hash---------=%s" % tx_hash)
            tx = self.wallet.db.get_transaction(tx_hash)
            if not tx:
                return False
            coins = self.wallet.get_spendable_coins(None, nonlocal_only=False)
            new_tx = self.wallet.bump_fee(tx=tx, new_fee_rate=new_fee_rate, coins=coins)
            size = new_tx.estimated_size()
            fee = new_tx.get_fee()
        except BaseException as e:
            raise BaseException(e)

        new_tx.set_rbf(self.rbf)
        out = {
            'new_tx': str(new_tx),
            'fee': fee
        }
        self.rbf_tx = new_tx
        # self.update_invoices(tx, new_tx.serialize_as_bytes().hex())
        return json.dumps(out)

    def confirm_rbf_tx(self, tx_hash):
        try:
            self.do_save(self.rbf_tx)
        except:
            pass
        try:
            if self.label_flag and self.wallet.wallet_type != "standard":
                self.label_plugin.push_tx(self.wallet, 'rbftx', self.rbf_tx.txid(), str(self.rbf_tx),
                                          tx_hash_old=tx_hash)
        except Exception as e:
            print(f"push_tx rbftx error {e}")
            pass
        return self.rbf_tx

    ###cpfp api###
    def get_rbf_or_cpfp_status(self, tx_hash):
        try:
            status = {}
            tx = self.wallet.db.get_transaction(tx_hash)
            if not tx:
                raise BaseException("tx is None")
            tx_details = self.wallet.get_tx_info(tx)
            is_unconfirmed = tx_details.tx_mined_status.height <= 0
            if is_unconfirmed and tx:
                # note: the current implementation of rbf *needs* the old tx fee
                if tx_details.can_bump and tx_details.fee is not None:
                    status['rbf'] = True
                else:
                    child_tx = self.wallet.cpfp(tx, 0)
                    if child_tx:
                        status['cpfp'] = True
            return json.dumps(status)
        except BaseException as e:
            raise e

    def get_cpfp_info(self, tx_hash, suggested_feerate=None):
        try:
            self._assert_wallet_isvalid()
            parent_tx = self.wallet.db.get_transaction(tx_hash)
            if not parent_tx:
                raise BaseException(FailedGetTx())
            info = {}
            child_tx = self.wallet.cpfp(parent_tx, 0)
            if child_tx:
                total_size = parent_tx.estimated_size() + child_tx.estimated_size()
                parent_txid = parent_tx.txid()
                assert parent_txid
                parent_fee = self.wallet.get_tx_fee(parent_txid)
                if parent_fee is None:
                    raise BaseException(_(
                        "CPFP(Child Pays For Parent) fails because it does not get the fee intormation of the original transaction."))
                info['total_size'] = '(%s) bytes' % total_size
                max_fee = child_tx.output_value()
                info['input_amount'] = self.format_amount(max_fee) + ' ' + self.base_unit

                def get_child_fee_from_total_feerate(fee_per_kb):
                    fee = fee_per_kb * total_size / 1000 - parent_fee
                    fee = min(max_fee, fee)
                    fee = max(total_size, fee)  # pay at least 1 sat/byte for combined size
                    return fee

                if suggested_feerate is None:
                    suggested_feerate = self.config.fee_per_kb()
                else:
                    suggested_feerate = suggested_feerate * 1000
                if suggested_feerate is None:
                    raise BaseException(
                        f'''{_("Failed CPFP(Child Pays For Parent)'")}: {_('dynamic fee estimates not available')}''')

                parent_feerate = parent_fee / parent_tx.estimated_size() * 1000
                info['parent_feerate'] = self.format_fee_rate(parent_feerate) if parent_feerate else ''
                info['fee_rate_for_child'] = self.format_fee_rate(suggested_feerate) if suggested_feerate else ''
                fee_for_child = get_child_fee_from_total_feerate(suggested_feerate)
                info['fee_for_child'] = util.format_satoshis_plain(fee_for_child, decimal_point=self.decimal_point)
                if fee_for_child is None:
                    raise BaseException("fee_for_child is none")
                out_amt = max_fee - fee_for_child
                out_amt_str = (self.format_amount(out_amt) + ' ' + self.base_unit) if out_amt else ''
                info['output_amount'] = out_amt_str
                comb_fee = parent_fee + fee_for_child
                comb_fee_str = (self.format_amount(comb_fee) + ' ' + self.base_unit) if comb_fee else ''
                info['total_fee'] = comb_fee_str
                comb_feerate = comb_fee / total_size * 1000
                comb_feerate_str = self.format_fee_rate(comb_feerate) if comb_feerate else ''
                info['total_feerate'] = comb_feerate_str

                if fee_for_child is None:
                    raise BaseException(_("Sub-transaction fee error."))  # fee left empty, treat is as "cancel"
                if fee_for_child > max_fee:
                    raise BaseException(_('Exceeding the Maximum fee limit.'))
            return json.dumps(info)
        except BaseException as e:
            raise e

    def create_cpfp_tx(self, tx_hash, fee_for_child):
        try:
            self._assert_wallet_isvalid()
            parent_tx = self.wallet.db.get_transaction(tx_hash)
            if not parent_tx:
                raise BaseException(FailedGetTx())
            new_tx = self.wallet.cpfp(parent_tx, self.get_amount(fee_for_child))
            new_tx.set_rbf(self.rbf)
            out = {
                'new_tx': str(new_tx)
            }

            try:
                self.do_save(new_tx)
                if self.label_flag and self.wallet.wallet_type != "standard":
                    self.label_plugin.push_tx(self.wallet, 'createtx', new_tx.txid(), str(new_tx))
            except BaseException as e:
                print(f"cpfp push_tx createtx error {e}")
                pass
            return json.dumps(out)
        except BaseException as e:
            raise e

    #######

    # network server
    def get_default_server(self):
        '''
        Get default electrum server
        :return: json like {'host':'127.0.0.1', 'port':'3456'}
        '''
        try:
            self._assert_daemon_running()
            net_params = self.network.get_parameters()
            host, port, protocol = net_params.server.host, net_params.server.port, net_params.server.protocol
        except BaseException as e:
            raise e

        default_server = {
            'host': host,
            'port': port,
        }
        return json.dumps(default_server)

    def set_server(self, host, port):
        '''
        Custom server
        :param host: host as str
        :param port: port as str
        :return: raise except if error
        '''
        try:
            self._assert_daemon_running()
            net_params = self.network.get_parameters()
            try:
                server = ServerAddr.from_str_with_inference('%s:%s' % (host, port))
                if not server: raise Exception("failed to parse")
            except BaseException as e:
                raise e
            net_params = net_params._replace(server=server,
                                             auto_connect=True)
            self.network.run_from_another_thread(self.network.set_parameters(net_params))
        except BaseException as e:
            raise e

    def get_server_list(self):
        '''
        Get Servers
        :return: servers info as json
        '''
        try:
            self._assert_daemon_running()
            servers = self.daemon.network.get_servers()
        except BaseException as e:
            raise e
        return json.dumps(servers)

    def rename_wallet(self, old_name, new_name):
        '''
        Rename the wallet
        :param old_name: old name as string
        :param new_name: new name as string
        :return: raise except if error
        '''
        try:
            self._assert_daemon_running()
            if old_name is None or new_name is None:
                raise BaseException(("Please enter the correct file name."))
            else:
                wallet = self.daemon._wallets[self._wallet_path(old_name)]
                wallet.set_name(new_name)
                wallet.db.set_modified(True)
                wallet.save_db()
        except BaseException as e:
            raise e

    def update_wallet_name(self, old_name, new_name):
        try:
            self._assert_daemon_running()
            if old_name is None or new_name is None:
                raise BaseException("Please enter the correct file name")
            else:
                os.rename(self._wallet_path(old_name), self._wallet_path(new_name))
                self.daemon.pop_wallet(self._wallet_path(old_name))
                self.load_wallet(new_name, password=self.android_id)
                self.select_wallet(new_name)
                return new_name
        except BaseException as e:
            raise e

    def select_wallet(self, name):
        '''
        Select wallet by name
        :param name: name as string
        :return: json like
        {
          "name": "",
          "label": "",
          "wallets": [
            {"coin": "eth", "balance": "", "fiat": ""},
            {"coin": "usdt", "balance": "", "fiat": ""}
          ]
        }
        '''
        try:
            self._assert_daemon_running()
            if name is None:
                self.wallet = None
            else:
                self.wallet = self.daemon._wallets[self._wallet_path(name)]

            self.wallet.use_change = self.config.get('use_change', False)

            if self.coins.__contains__(self.wallet.wallet_type[0:3]):
                PyWalib.set_server(self.coins[self.wallet.wallet_type[0:3]])
                addrs = self.wallet.get_addresses()
                checksum_from_address = self.pywalib.web3.toChecksumAddress(addrs[0])
                balance_info = self.wallet.get_all_balance(checksum_from_address,
                                                          self.coins[self.wallet.wallet_type[0:3]]['symbol'])

                wallet_balances = [
                    {
                        "coin": key,
                        **val,
                        "fiat": self.daemon.fx.format_amount_and_units(val['fiat'] * COIN) or f"0 {self.ccy}"
                    }
                    for key, val in balance_info.items()
                ]
                info = {
                    "name": name,
                    "label": self.wallet.get_name(),
                    "wallets": wallet_balances
                }
                return json.dumps(info, cls=DecimalEncoder)
            else:
                c, u, x = self.wallet.get_balance()
                print("console.select_wallet %s %s %s==============" % (c, u, x))
                print("console.select_wallet[%s] blance = %s wallet_type = %s use_change=%s add = %s " % (
                    self.wallet.get_name(), self.format_amount_and_units(c + u), self.wallet.wallet_type,
                    self.wallet.use_change,
                    self.wallet.get_addresses()))
                util.trigger_callback("wallet_updated", self.wallet)

                balance = c + u
                fait = self.daemon.fx.format_amount_and_units(balance) if self.daemon.fx else None
                fait = fait or f"0 {self.ccy}"
                info = {
                    "balance": self.format_amount(balance) + ' (%s)' % fait,  # fixme deprecated field
                    "name": name,
                    "label": self.wallet.get_name(),
                    "wallets": [
                        {"coin": "btc", "balance": Decimal(balance), "fiat": fait}
                    ]
                }
                if self.label_flag and self.wallet.wallet_type != "standard":
                    self.label_plugin.load_wallet(self.wallet)
                return json.dumps(info, cls=DecimalEncoder)
        except BaseException as e:
            raise BaseException(e)

    def sort_list(self, wallet_infos, type=None):
        try:
            from .sort_wallet_list import SortWalletList
            if len(wallet_infos) == 0:
                return wallet_infos
            sorted_wallet = SortWalletList(wallet_infos)
            if type is None:
                return wallet_infos
            if type == 'hd':
                return sorted_wallet.get_wallets_by_hd()
            if type == 'hw':
                return sorted_wallet.get_wallets_by_hw()
            else:
                return sorted_wallet.get_wallets_by_coin(coin=type)
        except BaseException as e:
            raise e

    def list_wallets(self, type=None):
        '''
        List available wallets
        :param type: None/hd/btc/eth
        :return: json like "[{"wallet_key":{'type':"", "addr":"", "name":"", "label":"", "device_id": ""}}, ...]"
        exp:
            all_list = testcommond.list_wallets()
            hd_list = testcommond.list_wallets(type='hd')
            hw_list = testcommond.list_wallets(type='hw')
            btc_list = testcommond.list_wallets(type='btc')
            eth_list = testcommond.list_wallets(type='eth')

        '''

        name_wallets = sorted([name for name in os.listdir(self._wallet_path())])
        name_info = {}
        for name in name_wallets:
            name_info[name] = self.local_wallet_info.get(name) if self.local_wallet_info.__contains__(name) else {
                'type': 'unknow', 'time': time.time(), 'xpubs': [], 'seed': ''}

        name_info = sorted(name_info.items(), key=lambda item: item[1]['time'], reverse=True)
        wallet_infos = []
        for key, value in name_info:
            if -1 != key.find(".tmp.") or -1 != key.find(".tmptest."):
                continue
            temp = {}
            wallet = self.daemon.wallets[self._wallet_path(key)]
            device_id = self.get_device_info(wallet) if isinstance(wallet.keystore, Hardware_KeyStore) else ""
            wallet_info = {'type': value['type'], 'addr': wallet.get_addresses()[0],
                           "name": self.get_unique_path(wallet), "label": wallet.get_name(), "device_id": device_id}
            temp[key] = wallet_info
            wallet_infos.append(temp)
        sort_info = self.sort_list(wallet_infos, type=type)
        return json.dumps(sort_info)

    def delete_wallet_from_deamon(self, name):
        try:
            self._assert_daemon_running()
            self.daemon.delete_wallet(name)
        except BaseException as e:
            raise BaseException(e)

    def has_history_wallet(self, wallet_obj):
        have_tx_flag = False
        if self.coins.__contains__(wallet_obj.wallet_type[0:3]):
            # address = wallet_obj.get_addresses()[0]
            # tx_list = self.get_eth_tx_list(wallet_obj, address)
            # if len(tx_list) != 0:
                #have_tx_flag = True
            print("")
        else:
            history = reversed(wallet_obj.get_history())
            for item in history:
                have_tx_flag = True
        return have_tx_flag

    def reset_config_info(self):
        self.local_wallet_info = {}
        self.config.set_key("all_wallet_type_info", self.local_wallet_info)
        self.derived_info = {}
        self.config.set_key("derived_info", self.derived_info)
        self.backup_info = {}
        self.config.set_key("backupinfo", self.backup_info)
        self.decimal_point = 5
        self.config.set_key("decimal_point", self.decimal_point)
        self.config.set_key("language", "zh_CN")
        self.config.set_key("sync_server_host", "39.105.86.163:8080")
        self.config.set_key("show_addr_info", {})

    def reset_wallet_info(self):
        '''
        Reset all wallet info when Reset App
        :return: raise except if error
        '''
        try:
            util.delete_file(self._wallet_path())
            util.delete_file(self._tx_list_path())
            self.reset_config_info()
            self.hd_wallet = None
            self.check_pw_wallet = None
            self.daemon._wallets.clear()
        except BaseException as e:
            raise e

    def delete_wallet_devired_info(self, wallet_obj):
        have_tx = self.has_history_wallet(wallet_obj)
        if not have_tx:
            # delete wallet info from config
            self.delete_devired_wallet_info(wallet_obj)

    def delete_wallet(self, password="", name="", hd=None):
        '''
        Delete (a/all hd) wallet
        :param password: Password as string
        :param name: Wallet key
        :param hd: True if you want to delete all hd wallet
        :return: None
        '''

        try:
            wallet = self.daemon._wallets[self._wallet_path(name)]
            if self.local_wallet_info.__contains__(name):
                wallet_info = self.local_wallet_info.get(name)
                wallet_type = wallet_info['type']

            if not wallet.is_watching_only() and -1 == wallet_type.find("-hw-"):
                self.check_password(password=password)
            if hd is not None:
                self.delete_derived_wallet()
            else:
                if (-1 != wallet_type.find('-hd-') or -1 != wallet_type.find('-derived-')) and -1 == wallet_type.find("-hw-"):
                    self.delete_wallet_devired_info(wallet)
                self.delete_wallet_from_deamon(self._wallet_path(name))
                self.local_wallet_info.pop(name)
                self.config.set_key('all_wallet_type_info', self.local_wallet_info)
            # os.remove(self._wallet_path(name))
        except Exception as e:
            raise BaseException(e)

    def _assert_daemon_running(self):
        if not self.daemon_running:
            raise BaseException(
                _("The background process does not start and it is recommended to restart the application."))
            # Same wording as in electrum script.

    def _assert_wizard_isvalid(self):
        if self.wizard is None:
            raise BaseException("Wizard not running")
            # Log callbacks on stderr so they'll appear in the console activity.

    def _assert_wallet_isvalid(self):
        if self.wallet is None:
            raise BaseException(_("You haven't chosen a wallet yet."))
            # Log callbacks on stderr so they'll appear in the console activity.

    def _assert_hd_wallet_isvalid(self):
        if self.hd_wallet is None:
            raise BaseException(UnavaiableHdWallet())

    def _assert_coin_isvalid(self, coin):
        if coin != "btc" and coin != "eth":
            raise BaseException("coin must btc/eth")

    def _on_callback(self, *args):
        util.print_stderr("[Callback] " + ", ".join(repr(x) for x in args))

    def _wallet_path(self, name=""):
        if name is None:
            if not self.wallet:
                raise ValueError("No wallet selected")
            return self.wallet.storage.path
        else:
            wallets_dir = join(self.user_dir, "wallets")
            util.make_dir(wallets_dir)
            return util.standardize_path(join(wallets_dir, name))

    def _tx_list_path(self, name=""):
        wallets_dir = join(self.user_dir, "tx_history")
        util.make_dir(wallets_dir)
        return util.standardize_path(join(wallets_dir, name))

all_commands = commands.known_commands.copy()
for name, func in vars(AndroidCommands).items():
    if not name.startswith("_"):
        all_commands[name] = commands.Command(func, "")

SP_SET_METHODS = {
    bool: "putBoolean",
    float: "putFloat",
    int: "putLong",
    str: "putString",
}
