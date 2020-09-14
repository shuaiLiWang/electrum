from decimal import Decimal
import time
import json
import tempfile
import shutil
from .console import AndroidCommands
from electrum import util
from electrum import constants
from electrum.simple_config import SimpleConfig
from electrum.util import (format_satoshis, format_fee_satoshis, parse_URI,
                           is_hash256_str, chunks, is_ip_address, list_enabled_bits)

from . import BixinTestCaseForTestnet

class TestConsole(BixinTestCaseForTestnet):

    def setUp(self):
        super().setUp()
        constants.set_testnet()
        self.electrum_path = tempfile.mkdtemp()
        self.config = SimpleConfig({'electrum_path': self.electrum_path})
        self.user_dir = tempfile.mkdtemp()
        self.testcommond = AndroidCommands(self.config, self.user_dir)
        self.testcommond.start()

    def tearDown(self):
        super().tearDown()
        self.testcommond.stop_loop()
        shutil.rmtree(self.electrum_path)
        shutil.rmtree(self.user_dir)
        constants.set_mainnet()

    def test_create_multi_wallet(self):
        name = 'hahahahhahh999'  # software wallet create seed:rocket omit review divert bomb brief mushroom family fatal limb goose lion
        password = "111111"
        m = 2
        n = 2
        xpub1='Vpub5grtuYJFuEqUDE1Faumgr5osg3dWyJ5PiJdCELdMM4ai6nSCPNiQ671FRnucQNnMN3eznVE3e8Ud7HnMQVKtKy2JHkDmiySbHbaEePZByYX'
        xpub2 ="Vpub5gyCX33B53xAyfEaH1Jfnp5grizbHfxVz6bWLPD92nLcbKMsQzSbM2eyGiK4qiRziuoRhoeVMoPLvEdfbQxGp88PN9cU6zupSSuiPi3RjEg"
        #self.testcommond.delete_wallet(name)
        self.testcommond.set_multi_wallet_info(name,m,n)
        self.testcommond.add_xpub(xpub1)
        self.testcommond.add_xpub(xpub2)
        # # testcommond.delete_xpub(xpub1)
        # # testcommond.delete_xpub(xpub2)
        # # ret = testcommond.get_cosigner_num()
        # # print("after num ===== %s" % ret)
        self.testcommond.create_multi_wallet(name)
        self.assertEqual('tb1qsynmgj63h024fqw7vy0d6rn43n7ucj9rztg0lrn4wu9g69zmw7vqnecfpu',
                         json.loads(self.testcommond.get_wallet_address_show_UI())['addr'])


