import sqlite3
import time

class TxDb:
    def __init__(self, path=""):
        self.path = path
        self.conn = None
        self.cursor = None
    
    @staticmethod
    def create_table(cursor):
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS txinfo (tx_hash TEXT PRIMARY KEY, address TEXT, psbt_tx TEXT, raw_tx Text, time INTEGER, faile_info TEXT)")

    def create_save_time_table(self, cursor):
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS txtimeinfo (tx_hash TEXT PRIMARY KEY, time INTEGER)")

    def create_save_fee_table(self, cursor):
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS receviedtxfeeinfo (tx_hash TEXT PRIMARY KEY, fee TEXT)")

    def connect_db(fun):
        def wrapfun(*args, **kwargs):
            try:
                self.conn = sqlite3.connect(self.path)
                self.cursor = self.conn.cursor()
                return fun(*args, **kwargs)
            except Exception as e:
                raise e
            finally:
                if self.cursor:
                    self.cursor.close()
                if self.conn:
                    self.conn.close()

    @connect_db
    def get_tx_info(self, address):
        try:
            self.create_table(self.cursor)
            self.cursor.execute("SELECT * FROM txinfo WHERE address=? ORDER BY time", (address,))
            result = self.cursor.fetchall()
            tx_list = []
            for info in result:
                tx_list.append(info)
            return tx_list
        except Exception as e:
            raise e

    @connect_db
    def add_tx_info(self, address, psbt_tx, tx_hash, raw_tx="", failed_info=""):
        try:
            self.create_table(self.cursor)
            self.cursor.execute("INSERT OR IGNORE INTO txinfo VALUES(?, ?, ?, ?, ?, ?)",
                           (tx_hash, address, str(psbt_tx), str(raw_tx), time.time(), failed_info))
            self.conn.commit()
        except Exception as e:
            e.__repr__()
            raise e
        
    ### API for tx time
    @connect_db
    def get_tx_time_info(self, tx_hash):
        try:
            self.create_save_time_table(self.cursor)
            self.cursor.execute("SELECT * FROM txtimeinfo WHERE tx_hash=?", (tx_hash,))
            result = self.cursor.fetchall()
            tx_list = []
            for info in result:
                tx_list.append(info)
            return tx_list
        except Exception as e:
            raise e
        
    def add_tx_time_info(self, tx_hash):
        try:
            self.create_save_time_table(self.cursor)
            self.cursor.execute("INSERT OR IGNORE INTO txtimeinfo VALUES(?, ?)",
                           (tx_hash, time.time()))
            self.conn.commit()
        except Exception as e:
            e.__repr__()
            raise e
        
    ### API for recevied tx fee
    def get_received_tx_fee_info(self, tx_hash):
        try:
            self.create_save_fee_table(self.cursor)
            self.cursor.execute("SELECT * FROM receviedtxfeeinfo WHERE tx_hash=?", (tx_hash,))
            result = self.cursor.fetchall()
            tx_list = []
            for info in result:
                tx_list.append(info)
            return tx_list
        except Exception as e:
            raise e
        
    def add_received_tx_fee_info(self, tx_hash, fee):
        try:
            self.create_save_fee_table(self.cursor)
            self.cursor.execute("INSERT OR IGNORE INTO receviedtxfeeinfo VALUES(?, ?)",
                           (tx_hash, fee))
            self.conn.commit()
        except Exception as e:
            e.__repr__()
            raise e
        
    # def get_tx_info(self, address):
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = sqlite3.connect(self.path)
    #         cursor = conn.cursor()
    #         self.create_table(cursor)
    #         cursor.execute("SELECT * FROM txinfo WHERE address=? ORDER BY time", (address,))
    #         result = cursor.fetchall()
    #         tx_list = []
    #         for info in result:
    #             tx_list.append(info)
    #         return tx_list
    #     except Exception as e:
    #         raise e
    #     finally:
    #         if cursor:
    #             cursor.close()
    #         if conn:
    #             conn.close()

    # def add_tx_info(self, address, psbt_tx, tx_hash, raw_tx="", failed_info=""):
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = sqlite3.connect(self.path)
    #         cursor = conn.cursor()
    #         self.create_table(cursor)
    #         cursor.execute("INSERT OR IGNORE INTO txinfo VALUES(?, ?, ?, ?, ?, ?)",
    #                        (tx_hash, address, str(psbt_tx), str(raw_tx), time.time(), failed_info))
    #         conn.commit()
    #     except Exception as e:
    #         e.__repr__()
    #         raise e
    #     finally:
    #         if cursor:
    #             cursor.close()
    #         if conn:
    #             conn.close()

    # ### API for tx time
    # def get_tx_time_info(self, tx_hash):
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = sqlite3.connect(self.path)
    #         cursor = conn.cursor()
    #         self.create_save_time_table(cursor)
    #         cursor.execute("SELECT * FROM txtimeinfo WHERE tx_hash=?", (tx_hash,))
    #         result = cursor.fetchall()
    #         tx_list = []
    #         for info in result:
    #             tx_list.append(info)
    #         return tx_list
    #     except Exception as e:
    #         raise e
    #     finally:
    #         if cursor:
    #             cursor.close()
    #         if conn:
    #             conn.close()

    # def add_tx_time_info(self, tx_hash):
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = sqlite3.connect(self.path)
    #         cursor = conn.cursor()
    #         self.create_save_time_table(cursor)
    #         cursor.execute("INSERT OR IGNORE INTO txtimeinfo VALUES(?, ?)",
    #                        (tx_hash, time.time()))
    #         conn.commit()
    #     except Exception as e:
    #         e.__repr__()
    #         raise e
    #     finally:
    #         if cursor:
    #             cursor.close()
    #         if conn:
    #             conn.close()

    # ### API for recevied tx fee
    
    # def get_received_tx_fee_info(self, tx_hash):
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = sqlite3.connect(self.path)
    #         cursor = conn.cursor()
    #         self.create_save_fee_table(cursor)
    #         cursor.execute("SELECT * FROM receviedtxfeeinfo WHERE tx_hash=?", (tx_hash,))
    #         result = cursor.fetchall()
    #         tx_list = []
    #         for info in result:
    #             tx_list.append(info)
    #         return tx_list
    #     except Exception as e:
    #         raise e
    #     finally:
    #         if cursor:
    #             cursor.close()
    #         if conn:
    #             conn.close()

    # def add_received_tx_fee_info(self, tx_hash, fee):
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = sqlite3.connect(self.path)
    #         cursor = conn.cursor()
    #         self.create_save_fee_table(cursor)
    #         cursor.execute("INSERT OR IGNORE INTO receviedtxfeeinfo VALUES(?, ?)",
    #                        (tx_hash, fee))
    #         conn.commit()
    #     except Exception as e:
    #         e.__repr__()
    #         raise e
    #     finally:
    #         if cursor:
    #             cursor.close()
    #         if conn:
    #             conn.close()