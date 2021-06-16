from unittest import TestCase
from unittest.mock import Mock

from electrum_gui.common.provider.chains.stc import STCJsonRPC, STCProvider
from electrum_gui.common.provider.data import (
    AddressValidation,
    EstimatedTimeOnPrice,
    PricesPerUnit,
    SignedTx,
    TransactionInput,
    TransactionOutput,
    UnsignedTx,
)


class TestSTCProvider(TestCase):
    def setUp(self) -> None:
        self.fake_chain_info = Mock()
        self.fake_coins_loader = Mock()
        self.fake_client_selector = Mock()
        self.provider = STCProvider(
            chain_info=self.fake_chain_info,
            coins_loader=self.fake_coins_loader,
            client_selector=self.fake_client_selector,
        )

    def test_verify_address(self):
        self.assertEqual(
            AddressValidation(
                "0xb61a35af603018441b06177a8820ff2a", "0xb61a35af603018441b06177a8820ff2a", is_valid=True, encoding=None
            ),
            self.provider.verify_address("0xb61a35af603018441b06177a8820ff2a"),
        )
        self.assertEqual(
            AddressValidation(
                "b61a35af603018441b06177a8820ff2a", "b61a35af603018441b06177a8820ff2a", is_valid=True, encoding=None
            ),
            self.provider.verify_address("b61a35af603018441b06177a8820ff2a"),
        )
        self.assertEqual(
            AddressValidation("", "", is_valid=False, encoding=None),
            self.provider.verify_address("0xb61a35af603018441b06177a8820ff2"),
        )
        self.assertEqual(
            AddressValidation("", "", is_valid=False, encoding=None),
            self.provider.verify_address("0xb61a35af603018441b06177a8820ff2ab"),
        )
        self.assertEqual(
            AddressValidation("", "", is_valid=False, encoding=None),
            self.provider.verify_address("0x0xb61a35af603018441b06177a8820ff"),
        )
        self.assertEqual(
            AddressValidation("", "", is_valid=False),
            self.provider.verify_address("0xb61a35af603018441b06177a8820ff"),
        )
        self.assertEqual(AddressValidation("", "", is_valid=False, encoding=None), self.provider.verify_address(""))
        self.assertEqual(AddressValidation("", "", is_valid=False, encoding=None), self.provider.verify_address("0x"))
        self.assertEqual(
            AddressValidation(
                "stc1pr9xnd0n9492jq8k8j9nt3r9p3cf88k6a7wtl9cyal2773ap3x6lpjnfkhej6j4fqrmreze4c3jscug7yx2e",
                "stc1pr9xnd0n9492jq8k8j9nt3r9p3cf88k6a7wtl9cyal2773ap3x6lpjnfkhej6j4fqrmreze4c3jscug7yx2e",
                is_valid=True,
                encoding="BECH32",
            ),
            self.provider.verify_address(
                "stc1pr9xnd0n9492jq8k8j9nt3r9p3cf88k6a7wtl9cyal2773ap3x6lpjnfkhej6j4fqrmreze4c3jscug7yx2e"
            ),
        )
        self.assertEqual(
            AddressValidation("", "", is_valid=False),
            self.provider.verify_address(
                "stc1pr9xnd0n9492jq8k8j9nt3r9p3cf88k6a7wtl9cyal2773ap3x6lpjnfkhej6j4fqrmreze4c3jscug7yx2"
            ),
        )
        self.assertEqual(
            AddressValidation(
                "stc1pr9xnd0n9492jq8k8j9nt3r9p3crvw030",
                "stc1pr9xnd0n9492jq8k8j9nt3r9p3crvw030",
                is_valid=True,
                encoding="BECH32",
            ),
            self.provider.verify_address("stc1pr9xnd0n9492jq8k8j9nt3r9p3crvw030"),
        )

    def test_pubkey_to_address(self):
        verifier = Mock(
            get_pubkey=Mock(
                return_value=bytes(
                    x for x in bytes.fromhex("63f3260100a7d409728984bfd3d8eac9a7a6dffb98db65fecc01d6df184f292a")
                )
            )
        )
        self.assertEqual(
            "0x194d36be65a955201ec79166b88ca18e",
            self.provider.pubkey_to_address(verifier=verifier),
        )
        verifier.get_pubkey.assert_called_once_with(compressed=False)
        self.assertEqual(
            "stc1pr9xnd0n9492jq8k8j9nt3r9p3cf88k6a7wtl9cyal2773ap3x6lpjnfkhej6j4fqrmreze4c3jscug7yx2e",
            self.provider.pubkey_to_address(verifier=verifier, encoding="BECH32"),
        )

    def test_fill_unsigned_tx(self):
        # external_address_a = "0xb61a35af603018441b06177a8820ff2a"
        # external_address_b = "0x194d36be65a955201ec79166b88ca18e"
        contract_address = "0xb61a35af603018441b06177a8820ff2a"

        fake_client = Mock(
            get_prices_per_unit_of_fee=Mock(
                return_value=PricesPerUnit(
                    slow=EstimatedTimeOnPrice(price=int(1)),
                    normal=EstimatedTimeOnPrice(price=int(1)),
                    fast=EstimatedTimeOnPrice(price=int(2)),
                )
            ),
            get_address=Mock(return_value=Mock(nonce=18)),
        )
        fake_jsonRPC = Mock(
            is_contract=Mock(side_effect=lambda address: address == contract_address),
            estimate_gas_limit=Mock(return_value=100000000),
        )

        def _client_selector_side_effect(**kwargs):
            instance_required = kwargs.get("instance_required")
            if instance_required and issubclass(instance_required, STCJsonRPC):
                return fake_jsonRPC
            else:
                return fake_client

        self.fake_client_selector.side_effect = _client_selector_side_effect

        with self.subTest("Empty UnsignedTx"):
            self.assertEqual(
                self.provider.fill_unsigned_tx(
                    UnsignedTx(),
                ),
                UnsignedTx(fee_limit=100000, fee_price_per_unit=int(1)),
            )
            fake_client.get_prices_per_unit_of_fee.assert_called_once()
            fake_client.get_address.assert_not_called()
            fake_jsonRPC.is_contract.assert_not_called()
            fake_jsonRPC.estimate_gas_limit.assert_not_called()

            fake_client.get_prices_per_unit_of_fee.reset_mock()

    def test_sign_transaction(self):
        self.fake_chain_info.chain_id = 251

        with self.subTest("Sign STC Transfer Tx"):
            fake_signer = Mock(
                sign=Mock(
                    return_value=(
                        bytes.fromhex(
                            "b4f0eb5b9994767f8e43885d4c50f5e066f14dee8c8c72bca1d717b392cb77d0738373e3bd1a7809c587afcbc8e31185bcdf0d288a63b01bca5eb7b713bed200"
                        ),
                        0,
                    )
                ),
                get_pubkey=Mock(
                    return_value=bytes(
                        x for x in bytes.fromhex("7b945271879962dde59a0e170219d04a1c3ae3901de95041283c473902d0b03d")
                    )
                ),
            )
            signers = {"0xb61a35af603018441b06177a8820ff2a": fake_signer}
            self.assertEqual(
                self.provider.sign_transaction(
                    self.provider.fill_unsigned_tx(
                        UnsignedTx(
                            inputs=[TransactionInput(address="0xb61a35af603018441b06177a8820ff2a", value=1024)],
                            outputs=[TransactionOutput(address="0x194d36be65a955201ec79166b88ca18e", value=1024)],
                            nonce=18,
                            fee_price_per_unit=1,
                            fee_limit=10000000,
                            payload={"expiration_time": 1621325706},
                        ),
                    ),
                    signers,
                ),
                SignedTx(
                    txid="0x55a8ab2df5db77e3be24304cc868f12fd35cb78e77842003d5f2aa17494adfd9",
                    raw_tx="0xb61a35af603018441b06177a8820ff2a120000000000000002000000000000000000000000000000010f5472616e73666572536372697074730c706565725f746f5f706565720107000000000000000000000000000000010353544303535443000310194d36be65a955201ec79166b88ca18e01001000040000000000000000000000000000809698000000000001000000000000000d3078313a3a5354433a3a5354438a77a36000000000fb00207b945271879962dde59a0e170219d04a1c3ae3901de95041283c473902d0b03d40b4f0eb5b9994767f8e43885d4c50f5e066f14dee8c8c72bca1d717b392cb77d0738373e3bd1a7809c587afcbc8e31185bcdf0d288a63b01bca5eb7b713bed200",
                ),
            )
