import json
import unittest
from os.path import join

from electrum import util
from electrum_gui.android.console import AndroidCommands


def get_wallet_path(name=""):
    wallets_dir = join(util.user_dir(), "wallets")
    util.make_dir(wallets_dir)
    return util.standardize_path(join(wallets_dir, name))


class Test_bitcoin_testnet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "111111"
        cls.mnemonic = "crash frost drive trigger render dizzy vacuum cement enact minute curve blanket"
        cls.btc_private = "L1ewnNAeZacGDNpT2L18Ga3dMFs6W735Ak3B2trjCp5n4vALfLRU"
        cls.eth_private = "0x14ddb0de338e530e3911fa365cf492a563241f081144d18a8bb6cdead42cda14"
        cls.pubkey = "030e7d0ed57c435f1bfd9461de54fe89e8ad0688eca6c9b9827cdc2b1511d27c90"
        cls.passphrase = ""
        util.delete_file(get_wallet_path())
        # constants.set_testnet()
        cls.testcommond = None
        cls.testcommond = AndroidCommands(android_id=cls.password)
        cls.testcommond.set_syn_server(True)

    def setUp(self):
        self.testcommond.reset_wallet_info()

    # def tearDown(self):
    #
    #     time.sleep(1)
    #     # self.testcommond.reset_wallet_info()

    @classmethod
    def tearDownClass(cls):
        cls.testcommond.stop()

    def test_recovery_hd_wallet_by_seed_and_derived_2_wallet_per_chain(self):
        wallet_list = []
        chain_list = ["btc", "eth", "bsc", "heco", "okt"]
        data = self.testcommond.create_hd_wallet(self.password, seed=self.mnemonic, create_coin=json.dumps(chain_list))
        for info in json.loads(data)["wallet_info"]:
            wallet_list.append(info['name'])

        self.assertTrue("d4b8ed619e6e0ffc033e2f1dfbdfcc9374e8a8a98c72a44b74b635e52b1ae195" in wallet_list)
        self.assertTrue("c77fd0012ca5136614c2cf4c55a3ebdf06ec26115a9282be2dda0be756d59e87" in wallet_list)
        self.assertTrue("06edeb2d54095bfb2530cdff09a4212167e326032304fe4afd438b8e38fc8806" in wallet_list)
        self.assertTrue("959bd8accaed77b1ef62c4dd5ee0184bfddd761868e6623ad7bbf575b760d8f6" in wallet_list)
        self.assertTrue("a56be4260e09180ab282a31bb617562d82fde4ce64d104b6e48ac465cd106544" in wallet_list)
        self.testcommond.recovery_confirmed(json.dumps(wallet_list))

        for chain in chain_list:
            derived_info = self.testcommond.create_derived_wallet("111", self.password, coin=chain)
            wallet_list.append(json.loads(derived_info)["wallet_info"][0]['name'])

        self.assertTrue("77e2175a24e8ac352d6772b5d99f0aa8abe2180def6bdb8df3bd976a81b9f09e" in wallet_list)
        self.assertTrue("42ea7dd33001050cb56726174a41c00244d693a5297e1065fff706f3cb76ee1c" in wallet_list)
        self.assertTrue("b6478fce0fbab068abfc288ef0c7dd9119dcbe7c97d0da9043427079ce524519" in wallet_list)
        self.assertTrue("0109836421a167a261974e67a36a2b2e4d480a32300b984b03c8537d94a728e3" in wallet_list)
        self.assertTrue("23932aacda52b2887af20431b405e9cfa9ae0355e125654d41d39617adb8f219" in wallet_list)

    def test_create_independent_wallet_by_import_seed_and_export_seed(self):
        wallet_id_info = [
            {"coin": "btc", "id": "898e3d5e0fc230a39c10ab5cb5d4ce913499ad9499269ac31ae7daa37cf359f2", "purpose": 44},
            {"coin": "btc", "id": "d4b8ed619e6e0ffc033e2f1dfbdfcc9374e8a8a98c72a44b74b635e52b1ae195", "purpose": 49},
            {"coin": "btc", "id": "77e2175a24e8ac352d6772b5d99f0aa8abe2180def6bdb8df3bd976a81b9f09e", "purpose": 84},
            {"coin": "eth", "id": "c77fd0012ca5136614c2cf4c55a3ebdf06ec26115a9282be2dda0be756d59e87", "purpose": 44},
            {"coin": "bsc", "id": "06edeb2d54095bfb2530cdff09a4212167e326032304fe4afd438b8e38fc8806", "purpose": 44},
            {"coin": "heco", "id": "959bd8accaed77b1ef62c4dd5ee0184bfddd761868e6623ad7bbf575b760d8f6", "purpose": 44},
            {"coin": "okt", "id": "a56be4260e09180ab282a31bb617562d82fde4ce64d104b6e48ac465cd106544", "purpose": 44},
        ]

        for wallet in wallet_id_info:
            wallet_info = json.loads(
                self.testcommond.create(
                    "test1", self.password, seed=self.mnemonic, purpose=wallet["purpose"], coin=wallet["coin"]
                )
            )
            self.assertEqual(wallet_info["wallet_info"][0]["name"], wallet["id"])
            self.testcommond.switch_wallet(wallet["id"])
            seed = self.testcommond.export_seed(self.password, wallet["id"])
            self.assertEqual(seed, self.mnemonic)
            now_wallet = self.testcommond.get_wallet_by_name(wallet["id"])
            now_wallet.stop()

    def test_create_customer_wallet_by_import_seed_path_and_export_seed(self):
        wallet_id_info = [
            {
                "coin": "btc",
                "id": "bdb38af5fc400c7469668373ebc2c00cfc2784d9fbc8aeb593d0ce62dc423623",
                "bip39_derivation": "m/44'/0'/15'/0/10",
            },
            {
                "coin": "btc",
                "id": "c0504f07fce3cb99392ed7f29d7d001d93f509a6e5fcbd29ac1fc984e533620a",
                "bip39_derivation": "m/49'/0'/13'/0/10",
            },
            {
                "coin": "btc",
                "id": "2813655ec023c6cd4e8d7731c941daa5a754a2918c6781a1d71a46e0948fda11",
                "bip39_derivation": "m/84'/0'/15'/0/10",
            },
            {
                "coin": "eth",
                "id": "38fab513f0893daec6113ba2dc21e731f10093f158a2008bb5b566cc568566ff",
                "bip39_derivation": "m/44'/0'/0'/0/10",
            },
            {
                "coin": "bsc",
                "id": "2e47a8ffb84208e54e0c1b58919aedfd9a7b19b4b18ad75dbd93fac04803217c",
                "bip39_derivation": "m/44'/0'/1'/0/1000",
            },
            {
                "coin": "heco",
                "id": "9c36a25b340d53d6905394e97d6d0cff2f95e40f5d8ea7c643839110fe7276eb",
                "bip39_derivation": "m/44'/0'/8'/8/10",
            },
            {
                "coin": "okt",
                "id": "a62396868b02a9ad6aac61a4cfb3356f45afa67f0763ea8dfbdb62cebeefd969",
                "bip39_derivation": "m/44'/10'/155'/0/10",
            },
        ]

        for wallet in wallet_id_info:
            wallet_info = json.loads(
                self.testcommond.create(
                    "test1",
                    self.password,
                    seed=self.mnemonic,
                    is_customized_path=True,
                    bip39_derivation=wallet["bip39_derivation"],
                    coin=wallet["coin"],
                )
            )
            self.assertEqual(wallet_info["wallet_info"][0]["name"], wallet["id"])
            self.testcommond.switch_wallet(wallet["id"])
            seed = self.testcommond.export_seed(self.password, wallet["id"])
            self.assertEqual(seed, self.mnemonic)
            now_wallet = self.testcommond.get_wallet_by_name(wallet["id"])
            now_wallet.stop()

    def test_import_private_and_export_private(self):
        wallet_id_info = [
            {
                "coin": "btc",
                "id": "bcfcac470d43809c59617fc9b90c88de8c184efc69814ab8cf296c0cf2dcbe66",
                "private": self.btc_private,
                "purpose": 44,
            },
            {
                "coin": "btc",
                "id": "d4b8ed619e6e0ffc033e2f1dfbdfcc9374e8a8a98c72a44b74b635e52b1ae195",
                "private": self.btc_private,
                "purpose": 49,
            },
            {
                "coin": "btc",
                "id": "f25dba951e1e6a1147135017c6b1138c7b809fce8dced50106151069b153061b",
                "private": self.btc_private,
                "purpose": 84,
            },
            {
                "coin": "eth",
                "id": "c77fd0012ca5136614c2cf4c55a3ebdf06ec26115a9282be2dda0be756d59e87",
                "private": self.eth_private,
                "purpose": 44,
            },
            {
                "coin": "bsc",
                "id": "06edeb2d54095bfb2530cdff09a4212167e326032304fe4afd438b8e38fc8806",
                "private": self.eth_private,
                "purpose": 44,
            },
            {
                "coin": "heco",
                "id": "959bd8accaed77b1ef62c4dd5ee0184bfddd761868e6623ad7bbf575b760d8f6",
                "private": self.eth_private,
                "purpose": 44,
            },
            {
                "coin": "okt",
                "id": "a56be4260e09180ab282a31bb617562d82fde4ce64d104b6e48ac465cd106544",
                "private": self.eth_private,
                "purpose": 44,
            },
        ]

        for wallet in wallet_id_info:
            wallet_info = json.loads(
                self.testcommond.create(
                    "test1", self.password, privkeys=wallet['private'], purpose=wallet["purpose"], coin=wallet["coin"]
                )
            )
            self.assertEqual(wallet_info["wallet_info"][0]["name"], wallet["id"])
            self.testcommond.switch_wallet(wallet["id"])
            privkey = self.testcommond.export_privkey(self.password)
            self.assertEqual(privkey, wallet["private"])
            now_wallet = self.testcommond.get_wallet_by_name(wallet["id"])
            now_wallet.stop()

    def test_create_independent_wallet_by_publikey(self):
        wallet_id_info = [
            # {"coin": "btc", "id": "bcfcac470d43809c59617fc9b90c88de8c184efc69814ab8cf296c0cf2dcbe66",
            #  "pubkey": self.pubkey, "purpose": 44},#todo:need select type by customer
            # {"coin": "btc", "id": "d4b8ed619e6e0ffc033e2f1dfbdfcc9374e8a8a98c72a44b74b635e52b1ae195",
            #  "pubkey": self.pubkey, "purpose": 49},
            {
                "coin": "btc",
                "id": "a989c5f982aef0ff81d064e56259276045375b1aea81ba93827bf314aef7e912",
                "pubkey": self.pubkey,
                "purpose": 84,
            },
            {
                "coin": "eth",
                "id": "a679934396363e0c593d3ca763ff1fa0a11c573795530dc8e44e4462bffb3f7b",
                "pubkey": self.pubkey,
                "purpose": 44,
            },
            {
                "coin": "bsc",
                "id": "d259b41b50e6024c474ed5f02d594a9b8d1edbde2cdc52ded9b5a74f18896097",
                "pubkey": self.pubkey,
                "purpose": 44,
            },
            {
                "coin": "heco",
                "id": "9386cf65e041c185b34253c73354e96e2ec280bc833502194b4c7b47c10d09e6",
                "pubkey": self.pubkey,
                "purpose": 44,
            },
            {
                "coin": "okt",
                "id": "ceefa74019fb5712b94a4f5a039bce16ed4ce7f2f1d004173776568860e2de3a",
                "pubkey": self.pubkey,
                "purpose": 44,
            },
        ]

        for wallet in wallet_id_info:
            wallet_info = json.loads(
                self.testcommond.create(
                    "test1", addresses=wallet['pubkey'], purpose=wallet["purpose"], coin=wallet["coin"]
                )
            )
            self.assertEqual(wallet_info["wallet_info"][0]["name"], wallet["id"])

            self.testcommond.switch_wallet(wallet["id"])
            try:
                self.testcommond.export_privkey(self.password)
                is_can_export_privkey = True
            except Exception:
                is_can_export_privkey = False
            self.assertFalse(is_can_export_privkey)

            try:
                self.testcommond.export_seed(self.password, wallet['id'])
                is_can_export_seed = True
            except Exception:
                is_can_export_seed = False
            self.assertFalse(is_can_export_seed)

            now_wallet = self.testcommond.get_wallet_by_name(wallet["id"])
            now_wallet.stop()

    def test_show_address_and_sign_message_verify_message_by_hw(self):
        wallet_id_info = [
            {
                "coin": "btc",
                "id": "aa6cdf2eafd3288379fcf61a1498f97cd5af3f2384733148d9c35df3fa293799",
                "purpose": "p2wpkh",
                "address": "bc1qmucgkmmghg0xj8ux2402c3h8h39fwk4vk7xztj",
            },
            {
                "coin": "btc",
                "id": "58cc14b20a7e31a8d8cd70109e589c7622c83c782a0976495910245f5a904dbf",
                "purpose": "p2pkh",
                "address": "1JEX9K4pwueSZ7LNbwrDMj8ZJ6TtAfPQtQ",
            },
            {
                "coin": "btc",
                "id": "466334fef9ea4b7c4e3e953a431341eac0eadff4c547ba8e35205df0aa1fb7b4",
                "purpose": "p2wpkh-p2sh",
                "address": "351DmMiCTGQaSLVQAZFqmvHvtRgeCqFDQi",
            },
            {
                "coin": "eth",
                "id": "0eda42ba5f0db9a184be55df137e940280e3fc71737f84ed882c49fd5b144fc7",
                "purpose": "p2pkh",
                "address": "0x9077b1164baeEDb35FE221171ef279E17053f259",
            },
            {
                "coin": "btc",
                "id": "f9d1bd4e6273d525922702cb405a1ab0ffb1c117734b16097505a55f9e8cb505",
                "purpose": "p2pkh",
                "address": "1HiyDmkrBf2QdjtLXA5bpD7zVtDhTkN8J8",
            },
        ]
        for wallet in wallet_id_info:
            xpub = self.testcommond.create_hw_derived_wallet(coin=wallet["coin"], _type=wallet["purpose"])
            wallet_info = json.loads(
                self.testcommond.import_create_hw_wallet("222", 1, 1, json.dumps([[xpub, ""]]), coin=wallet["coin"])
            )
            self.assertEqual(wallet_info["wallet_info"][0]["name"], wallet["id"])
            self.testcommond.switch_wallet(wallet["id"])
            address = json.loads(self.testcommond.get_wallet_address_show_UI())['addr']
            msg = "test"
            self.assertEqual(address, wallet["address"])
            sign_info = self.testcommond.sign_message(address, msg)
            result = self.testcommond.verify_message(address, msg, sign_info, coin=wallet["coin"])
            self.testcommond.show_address(address, coin=wallet["coin"])
            self.assertTrue(result)
            now_wallet = self.testcommond.get_wallet_by_name(wallet["id"])
            now_wallet.stop()

    # todo:need have sufficient balance for btc/eth/bsc/heco/bsc/okt
    # def test_send(self):
    #     self.assertEqual(self.mnemonic, self.mnemonic)
    #     wallet_id_info = [
    #         {"coin": "btc", "id": "1efe85dfe8a63fabfe2f55f1b8fb3f516ecd7d571cc3c52c681a4216a8091454", "purpose": 44},
    #         # {"coin": "btc", "id": "d4b8ed619e6e0ffc033e2f1dfbdfcc9374e8a8a98c72a44b74b635e52b1ae195", "purpose": 49},
    #         # {"coin": "btc", "id": "77e2175a24e8ac352d6772b5d99f0aa8abe2180def6bdb8df3bd976a81b9f09e", "purpose": 84},
    #         # {"coin": "eth", "id": "c77fd0012ca5136614c2cf4c55a3ebdf06ec26115a9282be2dda0be756d59e87", "purpose": 44},
    #         # {"coin": "bsc", "id": "06edeb2d54095bfb2530cdff09a4212167e326032304fe4afd438b8e38fc8806", "purpose": 44},
    #         # {"coin": "heco", "id": "959bd8accaed77b1ef62c4dd5ee0184bfddd761868e6623ad7bbf575b760d8f6", "purpose": 44},
    #         # {"coin": "okt", "id": "a56be4260e09180ab282a31bb617562d82fde4ce64d104b6e48ac465cd106544", "purpose": 44}
    #     ]
    #
    #     for wallet in wallet_id_info:
    #         wallet_info = json.loads(
    #             self.testcommond.create(
    #                 "test1", self.password, seed=self.mnemonic, purpose=wallet["purpose"], coin=wallet["coin"]
    #             )
    #         )
    #         id = wallet_info["wallet_info"][0]["name"]
    #         print(f"....id = {id}")
    #         # self.assertEqual(wallet_info["wallet_info"][0]["name"], wallet["id"])
    #         self.testcommond.switch_wallet(wallet["id"])
    #         addr = self.testcommond.get_wallet_address_show_UI()
    #         if wallet["coin"] == "btc":
    #             all_output = []
    #             output_info = {'bcrt1qq53vkwezxvuueyzmgdncj0p78qahg355gd720p': '1'}
    #             all_output.append(output_info)
    #             output_str = json.dumps(all_output)
    #             ret_str = self.testcommond.get_fee_by_feerate(output_str=output_str, feerate=10)
    #             ret_list = json.loads(ret_str)
    #             sign_tx = self.testcommond.sign_tx(ret_list['tx'], self.password)
    #             print("==========sign_tx = %s" % sign_tx)
    #             testinfo = self.testcommond.get_all_tx_list(None)
    #             print("----testinfo create = %s------------" % testinfo)
    #             data = json.loads(testinfo)
    #
    #             self.testcommond.get_tx_info()
    #
    #             sign = json.loads(sign_tx)
    #             self.testcommond.broadcast_tx(sign)
    #         now_wallet = self.testcommond.get_wallet_by_name(wallet["id"])
    #         now_wallet.stop()


if __name__ == '__main__':
    unittest.main(verbosity=2)
