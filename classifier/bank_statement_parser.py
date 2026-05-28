# -*- coding: utf-8 -*-
"""bank_statement_parser — categorisation engine for UK bank exports.

Copied from a private repository and redacted for public commit per
SPEC_AGENT.md §9. Personally identifying values (account numbers, employer
names, cleaner names, cardholder/card details, loan references, file
paths) have been replaced with stable placeholders that match the synthetic
data generator in data/synthetic/. The function bodies are otherwise
unchanged.

Phase 1 (SPEC §3.4) wraps `categories()` with a SQLite-first lookup;
see classifier/rule_lookup.py. The hardcoded chain below remains the
fallback for any memo the rules table doesn't match.

Note: the legacy `Bills/utilities/phone` (Skype/landline) sub-category has
been merged into `Mobile Phone` per SPEC §4's updated taxonomy.
"""
import csv
import sys
import pandas as pd
import os.path
import re
import argparse as ap
import logging
from datetime import datetime, date, timedelta
import os
import glob
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# helper functions
def process_amount_sainsbury(df):
    if df["Memo"] == "DIRECT DEBIT RECEIVED, THANK YOU":
        return df["Amount"]
    else:
        return -(df["Amount"])

# function to validate account number.
def set_up_account_number(df):
    #Input df as dataframe
    # return series with the account type.
    if df["Account Number"] == "Blue Rewards":
        return "ACCOUNT_CURRENT"
    else:
        return df["Account Number"]

# function to fill in account name and account type.
def set_up_account_type(df):

    # return the account type.
    if df["Account Number"] == "ACCOUNT_CURRENT":
        return "Current Account"
    elif df["Account Number"] == "ACCOUNT_SAVINGS":
        return "Savings"
    elif df["Account Number"] == "ACCOUNT_SAVINGS_LEGACY_1":
        return "Savings"
    elif df["Account Number"] == "ACCOUNT_SAVINGS_LEGACY_2":
        return "Savings"
    elif df["Account Number"] == "ACCOUNT_BARCLAYCARD":
        return "Credit Card"
    elif df["Account Number"] == "ACCOUNT_AMEX":
        return "Credit Card"
    elif df["Account Number"] == "ACCOUNT_SAINSBURY":
        return "Credit Card"
    else:
        return ""

def set_up_account_name(df):
    #Input df as dataframe
    # return the account name.
    if df["Account Number"] == "ACCOUNT_CURRENT":
        return "Current"
    # but, if not, return it multiplied by 100
    elif df["Account Number"] == "ACCOUNT_SAVINGS":
        return "Pot of Gold"
    elif df["Account Number"] == "ACCOUNT_BARCLAYCARD":
        return "Barclaycard"
    elif df["Account Number"] == "ACCOUNT_AMEX":
        return "Amex"
    elif df["Account Number"] == "ACCOUNT_SAINSBURY":
        return "Sainsbury"
    else:
        return ""


def categories(df):
    # print(df['Memo'])  # Commented out - uncomment for debugging if needed

    # Withdrawals
    if (df['Account Number'] == 'ACCOUNT_CURRENT') & (df['Type'] == 'CASH'):
        return  pd.Series(['Withdrawal',None, None, None])
    # Withdrawals - ATM patterns (for non-CASH types from credit cards)
    elif bool(re.match('.*CARDTRONICS|.*NOTEMACHINE|.*NAT WEST BANK.*NWB|.*TESCO.*TESCO FO|.*SAINSBURYS BANK.*Sainsburys Bank', df['Memo'], re.IGNORECASE)):
        return pd.Series(['Withdrawal', None, None, None])

    #############
    # Income
    #############
    elif (bool(re.match('COMPANY_A|COMPANY_B|COMPANY_C',df['Memo']))):
        return pd.Series(['Income','Salary', None,None])


    #############
    # House
    #############
    # House - Mortgage
    elif ((df['Account Number'] == 'ACCOUNT_CURRENT') & bool(re.match('^MTG|ACCORD MORTGAGES', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['House' ,'Mortgage', None, None])
    # House - Renovation
    elif bool(re.match('BARCLAYS PRTNR FIN',df['Memo'],re.IGNORECASE)):
        return pd.Series(['House','Maintenance','kitchen/bathroom','wren repayment'])
    # House - Maintenance
    elif bool(re.match('.*EUROKEN SUPPLIES|.*TOOLSTATION|.*BUILDERDEPO|.*SORODOC|BUILDER DEPOT|M P MORAN & SONS|SCREWFIX|WICKES|B & Q|B AND Q|B&Q',df['Memo'],re.IGNORECASE)):
        return pd.Series(['House','Maintenance','TBC','TBC'])
    # House - Maintenance - lighting
    elif bool(re.match('.*HOMARY',df['Memo'],re.IGNORECASE)):
        return pd.Series(['House','Maintenance','TBC','lighting'])

    #############
    # Shopping
    #############
    # Shopping - Groceries
    elif (bool(re.match(".*GAILS|.*GAIL'S|IRISH MEAT MARKET|.*O'Farrells|^HARRYS", df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Groceries',None, None])
        # Shopping - Groceries - supermarket
    elif (bool(re.match(".*SAINSBURY'S S|.*SAINSBURYS TO YOU|.*SAINSBURYS S|.*TESCO SELF SERVICE|.*TESCO STORE|.*TESCO SUBSCRIPTION|.*WAITROSE.*|.*MORRISONS.*|.*MARKS.*SPENCER.*|.*M&S.*|.*M & S.*", df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Groceries','Supermarket', None])
    # Shopping - Groceries - Corner Shop
    elif (bool(re.match('^SAVERS HEALTH & BE|.*EURO SUPERMARKET|.*SUPERSAVE|.*GOODGE STREET NEWS|.*FOOD|.*YOUR LOCAL STORE|.*NASH SUPERMARKET|.*CORNER XPRESS|.*EUROSUPERMARKET|.*MUNRO|.*FURNESS STORE|.*CASTLE NEWS|.*FASHION AND MEDIA|.*SHALPRIKA|.*MURCO S/STN|.*THE KENSAL STORE|.*EAST FINCH.*SUPER|.*HARROW ROAD CONVEN|.*GURU KRUPA RETAIL|.*DIAMOND NEWS|.*NISA LOCAL|.*CAVENDISH CANDY|.*SWEET TOUCH|.*NEW FASHION MEDIA NEWS|.*SJ NEWS|.*STREET NEWS|.*TEX COLLEGE PARK', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Groceries','Corner Shop', None])
        # Shopping - Groceries - veg box
    elif (bool(re.match('.*RIVERFORD', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Groceries','veg box', None])
    # Shopping - Groceries - wine
    elif (bool(re.match('JAZZ WINE MART|NAKED WINES', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Groceries','wine', None])
    # Shopping - household
    elif (bool(re.match('.*HOMESENSE|.*EBAY', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Household', None, None])
    # Shopping - household - DIY
    elif (bool(re.match(r'.*B\s*&\s*Q|.*B\s*AND\s*Q|.*HOMEBASE|.*SCREWFIX', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Household','DIY', None])
    # Shopping - clothes
    elif (bool(re.match(r'.*TK\s*MAXX|.*T\s*K\s*MAXX|.*DYNAMIC.*DRY\s*CLEAN|.*SPORTSDIRECT|.*SPORTS\s*DIRECT|.*CHARLES\s*TYRWHITT|.*UNIQLO|.*ZARA', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'Clothes', None, None])
    # Credit card payment
    elif (bool(re.match('.*CARDHOLDER_NAME_CARDNUMBER|.*Payment, Thank You|^PAYMENT RECEIVED - THANK YOU|.*AMERICAN EXPRESS|.*DIRECT DEBIT RECEIVED, THANK YOU|SAINSBURYS BANK PL', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping' ,'CreditCard', None, None])
    # Shopping - electronics
    elif (bool(re.match('^CURRYS', df['Memo'],re.IGNORECASE))):
        return  pd.Series(['Shopping','electronics','camera',None])


    #############
    # Transport
    #############
    # Transport - automotive - petrol
    elif bool(re.match('.*PETROL|.*SHELL|TESCO PAY AT PUMP|TESCO PFS|TESCO PAYAT PUMP',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Transport','Automotive','Petrol',None])
    # transport - taxi
    elif bool(re.match('.*UBER.*TRIP|.*CURB MOBILITY|.*CABVISION|.*VERIFONE|.*CMT UK LTD',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Transport','taxi',None,None])
    # Transport - automotive - road tax
    elif bool(re.match('.*DVLA VEHICLE TAX|DVLA AMEX VEHICLE TAX|WWW.DVLA.GOV.UK',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Transport','Automotive','Road tax',None])
    # Transport - automotive - parking & fees
    elif bool(re.match(r'.*PAY\s*BY\s*PHONE|.*APCOA|.*JUSTPARK|.*RING\s*GO',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Transport','Automotive','parking & fees',None])
    # Transport - tube
    elif bool(re.match('.*LUL TICKET MACHINE|.*OYSTERCARD|.*TFL TRAVEL CH|.*TFL.GOV.UK',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Transport','tube',None,None])


    #############
    # Leisure
    #############
    # Leisure - food/drinks - café - PRET (price-based: under £5 = café, £5+ falls through to restaurant)
    elif (bool(re.match('.*PRET.*MANGER|.*PRET A MANGER',df['Memo'],re.IGNORECASE))) & (abs(df['Amount']) < 5):
        return pd.Series(['Leisure','food/drinks','café', None])
    # Leisure - food/drinks - restaurants
    elif (bool(re.match("CONNECTCATERING.CO|.*CHIPOTLE|.*PAPA-DUM STREET|.*L_ANGOLO|.*H.T. Harris|.*DELIVEROO|.*JUST EAT.CO.UK|.*JUST-EAT.CO.UK|UBER EATS HELP.UBER.COM|LEON EASTCASTLE STREET|.*TORTILLA|.*PRET A MANGER|.*CARLUCCIO'S MARKET PLAC|.*VITAL INGREDIENT|.*AMICI MIEI|.*MCDONALDS|.*THE KATI ROLL|.*BANH MI BAY|.*FRANCO MANCA|.*PIZZA EXPRESS|.*KFC|.*BURGER KING|.*NANDO|.*ZIZZI|.*GOURMET BURGER|.*GBK|.*HONEST BURGER|.*PING PONG|.*KEU LONDON",df['Memo'],re.IGNORECASE))):
        return pd.Series(['Leisure','food/drinks','restaurant', None])
    # Leisure - food/drinks - pubs
    elif (bool(re.match(".*THE ISLAND|.*Earl of Camden|.*THE WOODMAN|.*Big Red|.*BREWDOG|.*BULL & BUSH|.*CAMDEN HEAD|.*CHAMBERLAYNE PUB|.*CHAMPION|.*COCK TAVERN|.*CROWN & SCEPTRE|.*FITZROY TAVERN|.*ISLAND|.*MARKET PLACE|.*MASONS ARMS|.*NORTHUMBERLAND|.*OLD WHITE LION|.*THE OLD NICK|.*TWO DOORS DOWN|.*WILLIAM IV|.*ROSE AND CROWN|.*OLD BULL & BUSH|.*ONEILL'S|.*Aces and Eights|.*The Champion|.*The Mossy Well|.*The Queensbury|.*ADAM & EVE|.*BLUE POST|.*ADAM AND EVE|.*BLACK ORCHID THAI|.*CGK EVENTS LIMITED|.*COCK AND LION LONDON GBR|.*DUKE OF YORK|.*Devonshire Arms London|.*ESSEX ARMS|.*GIPSY QUEEN|.*IZ.*AIN.*T NOTHIN B|.*JACK HORNER LONDON|.*JUNCTION TAVERN|.*JUST DRINKS|.*Ku Bar|.*Lucy Wong|.*MAID OF MUSWELL|.*MARQUIS OF GRANBY RATHB LONDON|.*PEMBROKE|.*PHOENIX|.*Prince Of Wales|.*RAILWAY|.*ROUND APP LTD|.*ROYAL OAK|.*SALUSBURY|.*SAWYERS ARMS LONDON|.*SPREAD EAGLE CAMDE|.*THE GALLERY|.*THE HOPE LONDON|.*THE JOHN BAIRD|.*THE LONDON EDITION|.*THE NEWMAN ARMS|.*THE SHIP|.*TRUMANS BEER|.*The William|.*UNICORN|.*YORKSHIRE GREY",df['Memo'],re.IGNORECASE))):
        return pd.Series(['Leisure','food/drinks','pub', None])
     # Leisure - food/drinks - cafe
    elif (bool(re.match('.*Black Sheep Co|.*BLACK SHEEP COFFEE|.*CAFFE NERO|.*COSTA|.*FIX COFFEE|.*KAFFEINE|LA GENOISE|ORTAKLAR LTD|REYNOLDS CAFE|BLANK STREET|.*STARBUCKS.*|.*GREGGS.*|.*SHARD.*|.*TRIPLE TWO COFF.*',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Leisure','food/drinks','café',None])
    # Leisure - others
    elif (bool(re.match('^NATIONAL LOTTERY|^STARS |UDEMY',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Leisure','entertainment',None,None])
    # Leisure - sport - gym
    elif (bool(re.match(r'.*STUDIO[\s-]*SOCIETY|.*HARLANDS.*STUDIO|.*1\s*LIFE|.*ONELIFE|.*PURE\s*GYM|.*EASY\s*GYM',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Leisure','sport','gym',None])

    #############
    # BANKING
    #############
    # savings transfer
    elif bool(re.match('.*ACCOUNT_SAVINGS|.*ACCOUNT_CURRENT|.*CARDHOLDER_NAME REGULAR SAVINGS|.*POT OF GOLD.*',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Savings','Transfer',None, None])
    # savings interest
    elif bool(re.match('INTEREST PAID GROSS|.*INT EARNED',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Savings','Interest',None, None])


    #############
    # HEALTH
    #############
     # Health - dentist
    elif bool(re.match('COLNEY HATCH DENTAL',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Health','Dentist',None,None])
    # Health - eyecare
    elif bool(re.match('SPECSAVERS',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Health','Eyecare','glasses',None])
    # Health - medicine
    elif bool(re.match('^BOOTS',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Health','General','Medicine',None])
    # Health - GP
    elif bool(re.match('^PUSHDOCTOR',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Health','GP',None,None])

    #############
    # BILLS
    #############
    # bills - water
    elif bool(re.match('THAMES WATER|AFFINITY WATER',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Bills','utilities','water', None])
    # bills - gas/electricity
    elif bool(re.match('E.ON|EDF ENERGY',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Bills','utilities','gas/elec', None])
    # bills - council tax
    elif bool(re.match('L B BRENT COUNCIL|BARNET BOROUGH COU|HARINGEY COUNCIL|WWW.BRENTGOV.UK|LBB COUNCIL TAX DD',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Bills','utilities','council tax', None])
    # bills - mobile phone (Skype/landline era merged into Mobile Phone per SPEC §4)
    elif bool(re.match('.*SKYPE',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Bills','utilities','Mobile Phone', None])
    # bills - mobile phone
    elif bool(re.match('VODAFONE|O2|H3G|CARPHONE WAREHOUSE|TELEFONICA UK|WWW.THREE.CO.UK',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Bills','utilities','Mobile Phone',None])
    # bills - TV license
    elif bool(re.match('TV LICENSE',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Bills','utilities','TV License', None])
    # bills - bank fees
    elif (bool(re.match('BLUE REWARDS|Loyalty Reward|EXPERIAN UK|^CHARGES|^COMMISSION|^INTEREST CHARGE',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Bills','Bank Fees', None, None])
     # bills - broadband
    elif (bool(re.match('PNET3462156|SKY DIGITAL|PLUSNET',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Bills','utilities','broadband', None])
    # bills - charity
    elif (bool(re.match(r'.*BRITISH\s*RED\s*CROSS|.*RED\s*CROSS|.*UNICEF',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Bills','Charity', None, None])
    # bills - household - cleaner
    elif (bool(re.match(r'.*CLEANER_A|.*CLEANER_B|.*CLEANER_C',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Bills','Household','cleaner', None])
    elif (bool(re.match('^LENDER_NAME_REFERENCE',df['Memo'],re.IGNORECASE))):
        return pd.Series(['Bills','loan',None, None])


    #############
    # SUBSCIPTION
    #############
    # subscription - amazon
    elif bool(re.match('.*AMAZON PRIME|.*PRIME VIDEO',df['Memo'],re.IGNORECASE)):
        return pd.Series(['Leisure','subscription','amazon', None])
    # subscriptions - music
    elif bool(re.match('.*SPOTIFY',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Leisure','subscription','music', None])
    # subscriptions - newspapers
    elif bool(re.match('THE ECONOMIST',df['Memo'],re.IGNORECASE)):
        return  pd.Series(['Leisure','subscription','newspapers', None])
    else:
        return  pd.Series(["Missing", None, None, None])


class Budget:
    def __init__(self, date):
        self.data = pd.DataFrame(columns=["Date", "Account Number", "Amount", "Type", "Memo"])
        self.date = date
        # Set up filenames - use environment variable or default to ./data
        budget_data_dir = os.environ.get('BUDGET_DATA_DIR', './data')
        self.path_to_tmp = os.path.join(budget_data_dir, 'tmp_data') + os.sep
        self.file_amex = self.path_to_tmp + "%s_amex.csv" % self.date
        self.file_barclaycard = self.path_to_tmp + "%s_credit_card.csv" % self.date
        self.file_current_account =  self.path_to_tmp + "%s_accounts_download.csv" % self.date
        self.file_preprocessed =  self.path_to_tmp + "%s_accounts_preprocessed.csv" % self.date
        self.file_sainsbury = self.path_to_tmp + "%s_sainsbury.csv" % self.date
        # NEW: Excel file path for Phase 4
        self.file_excel = os.path.join(budget_data_dir, 'budget.xlsx')
        # Set up headers for raw data
        self.headers_sainsbury = ["Date", "Memo", "Amount"]
        self.headers_amex_old = ["Date", "Reference", "Amount", "Memo", "Label", "log"]
        self.headers_amex = ["Date", "Memo", "Amount"]
        self.headers_barclaycard = ["Date", "Memo", "Label", "log", "Reference", "Credit", "Debit"]
        self.headers_current_account = ["Number", "Date", "Account Number", "Amount", "Type", "Memo"]

    def combine_and_rename_files(self):
        """Combine all data*.csv into <date>_accounts_download.csv
           and rename activity.csv into <date>_amex.csv.

           Skips if target files already exist to avoid overwriting."""
        try:
            logging.info("Budget   : Starting combination and rename process")
            os.makedirs(self.path_to_tmp, exist_ok=True)

            # Use existing file paths from __init__
            # self.file_current_account and self.file_amex are already defined

            # Combine all data*.csv files (only if target doesn't exist)
            if os.path.exists(self.file_current_account):
                logging.info(f"Budget   : {self.file_current_account} already exists - skipping combine")
            else:
                pattern = os.path.join(self.path_to_tmp, "data*.csv")
                data_files = sorted(glob.glob(pattern))

                if not data_files:
                    logging.warning("Budget   : No data*.csv files found")
                else:
                    combined_df = pd.concat([pd.read_csv(f) for f in data_files], ignore_index=True)
                    combined_df.to_csv(self.file_current_account, index=False)
                    logging.info(f"Budget   : Combined data saved as {self.file_current_account}")

            # Rename activity.csv (only if target doesn't exist)
            if os.path.exists(self.file_amex):
                logging.info(f"Budget   : {self.file_amex} already exists - skipping rename")
            else:
                activity_src = os.path.join(self.path_to_tmp, "activity.csv")
                if os.path.exists(activity_src):
                    os.rename(activity_src, self.file_amex)
                    logging.info(f"Budget   : Renamed {activity_src} -> {self.file_amex}")
                else:
                    logging.warning("Budget   : activity.csv not found — skipping rename")

        except Exception as e:
            logging.exception(f"Budget   : Error combining or renaming files: {e}")

    # Funtion to import amex raw data into database
    def import_amex(self):
        df = pd.read_csv(self.file_amex, header=0, names=self.headers_amex)
        # Set up account number and payment type
        df["Account Number"] = "ACCOUNT_AMEX"
        df["Type"] = "PAYMENT"

        # Calculate Amount
        df[["Amount"]] = df[["Amount"]].apply(pd.to_numeric, errors='coerce')
        df["Amount"] = -df["Amount"]
        # reorder columns
        self.data = self.data._append(df[["Date", "Account Number", "Amount", "Type", "Memo"]])

    # Funtion to import sainsbury raw data into database
    def import_sainsbury(self):
        df = pd.read_csv(self.file_sainsbury, encoding = "ISO-8859-1", header=None, names=self.headers_sainsbury)
        # Set up account number and payment type
        df["Account Number"] = "ACCOUNT_SAINSBURY"
        df["Type"] = "PAYMENT"
        # Calculate Amount
        df["Amount"] = df.apply(process_amount_sainsbury, axis=1)

        # reorder columns
        self.data = self.data._append(df[["Date", "Account Number", "Amount", "Type", "Memo"]])

    # Funtion to import barclaycard raw data into database
    def import_barclaycard(self):
        df = pd.read_csv(self.file_barclaycard, encoding = "ISO-8859-1", header=None, names=self.headers_barclaycard)
        # Reformat date
        df['Date'].replace(regex=True,inplace=True,to_replace=
                        (r' Jan ',r' Feb ',r' Mar ',r' Apr ',r' May ',r' Jun ',r' Jul ',r' Aug ',r' Sep ',r' Oct ',r' Nov ',r' Dec '),
                        value=(r'/01/20',r'/02/20',r'/03/20',r'/04/20',r'/05/20',r'/06/20',r'/07/20',r'/08/20',r'/09/20',r'/10/20',r'/11/20',r'/12/20'))

        # Set up account number and payment type
        df["Account Number"] = "ACCOUNT_BARCLAYCARD"
        df["Type"] = "PAYMENT"
        # Calculate Amount - Convert string used for credit and debit into float
        df.fillna(0, inplace=True)
        #df['Credit'].replace(regex=True,inplace=True,to_replace=(r'-',r','),value=(r'',r''))
        df[["Debit"]] = df[["Debit"]].apply(pd.to_numeric, errors='coerce')
        df[["Credit"]] = df[["Credit"]].apply(pd.to_numeric, errors='coerce')
        df["Amount"] = -(df["Credit"] + df["Debit"])
        # reorder columns
        self.data = self.data_append(df[["Date", "Account Number", "Amount", "Type", "Memo"]])

    # Funtion to import current account raw data into database
    def import_current_account(self):
        df = pd.read_csv(self.file_current_account,header=0)
        df.rename(columns={'Account':'Account Number' ,'Subcategory':'Type'},inplace=True)
        # Remove number column
        del df["Number"]
        self.data = self.data._append(df[["Date", "Account Number", "Amount", "Type", "Memo"]])

    # Add Account description columns
    def append_columns (self):
        self.data["Account Currency"] = "£"
        self.data['Account Number'] = self.data.apply(set_up_account_number, axis=1)
        self.data['Account Type'] = self.data.apply(set_up_account_type, axis=1)
        self.data['Account Name'] = self.data.apply(set_up_account_name, axis=1)
        self.data[['Category - Main','Category - Sub','Category - Sub2','Details']] = self.data.apply(categories, axis=1)


    # import raw data
    def import_raw_data(self):
        # append all accounts download together
        if os.path.exists(self.file_current_account):
            self.import_current_account()
        if os.path.exists(self.file_amex):
            self.import_amex()
        if os.path.exists(self.file_barclaycard):
            self.import_barclaycard()
        if os.path.exists(self.file_sainsbury):
            self.import_sainsbury()

        self.append_columns()

    def export_preprocessed_data(self):
        self.data.to_csv(self.file_preprocessed, index=False)

    # ========== PHASE 4: EXCEL INCREMENTAL IMPORT METHODS ==========

    def cleanup_old_backups(self, excel_path, days=90):
        """
        Remove Excel backups older than specified days.

        Args:
            excel_path: Path to Excel file (e.g., budget.xlsx)
            days: Number of days to keep backups (default: 90)
        """
        import glob
        from pathlib import Path

        base_path = Path(excel_path).parent
        base_name = Path(excel_path).stem  # e.g., "budget"

        # Find all backups
        pattern = str(base_path / f"{base_name}_backup_*.xlsx")
        backups = glob.glob(pattern)

        cutoff_date = datetime.now() - timedelta(days=days)
        deleted = 0

        for backup in backups:
            try:
                # Extract timestamp from filename (format: YYYYMMDD_HHMMSS)
                timestamp_str = backup.split('_backup_')[1].replace('.xlsx', '')
                backup_date = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')

                if backup_date < cutoff_date:
                    os.remove(backup)
                    deleted += 1
                    logging.info(f"Budget   : Deleted old backup: {backup}")
            except Exception as e:
                logging.warning(f"Budget   : Could not parse backup date for {backup}: {e}")

        if deleted > 0:
            logging.info(f"Budget   : Cleaned up {deleted} backups older than {days} days")

    def analyze_overlap(self, existing_overlap, new_overlap, overlap_date, account):
        """
        Analyze overlap date to determine action.

        Args:
            existing_overlap: DataFrame of existing transactions on overlap date
            new_overlap: DataFrame of new transactions on overlap date
            overlap_date: The overlap date (pandas Timestamp)
            account: Account number string

        Returns:
            dict: {'action': 'exact_match' | 'all_new' | 'override', ...}
        """
        compare_cols = ['Date', 'Account Number', 'Amount', 'Type', 'Memo']

        # Create comparison keys
        existing_keys = existing_overlap[compare_cols].copy()
        new_keys = new_overlap[compare_cols].copy()

        # Merge to find matches
        merged = new_keys.merge(
            existing_keys,
            on=compare_cols,
            how='left',
            indicator=True
        )

        matches = (merged['_merge'] == 'both').sum()
        new_only = (merged['_merge'] == 'left_only').sum()

        # Decision tree
        if matches == len(new_overlap) and len(new_overlap) == len(existing_overlap):
            # Perfect match: all transactions match, same count
            return {'action': 'exact_match', 'matches': matches}

        elif new_only == len(new_overlap):
            # All new: no matching transactions (brand new data)
            return {'action': 'all_new', 'new_only': new_only}

        else:
            # Partial match OR count mismatch
            # User requirement: Override ONLY if new_count > old_count
            if len(new_overlap) > len(existing_overlap):
                return {
                    'action': 'override',
                    'matches': matches,
                    'new_only': new_only,
                    'old_count': len(existing_overlap),
                    'new_count': len(new_overlap)
                }
            else:
                # new_count <= old_count: Treat as exact match (don't override)
                logging.warning(
                    f"Budget   : Account {account}: Overlap date {overlap_date.strftime('%Y-%m-%d')} "
                    f"has new_count ({len(new_overlap)}) <= old_count ({len(existing_overlap)}). "
                    f"Treating as exact match - skipping overlap date."
                )
                return {
                    'action': 'exact_match',
                    'matches': matches,
                    'warning': 'new_count <= old_count'
                }

    def process_account(self, account, existing_df, new_df):
        """
        Process a single account with overlap detection.

        Args:
            account: Account number string
            existing_df: Full existing DataFrame
            new_df: Full new DataFrame

        Returns:
            dict: {
                'rows_to_delete': DataFrame,
                'rows_to_add': DataFrame,
                'rows_added': int,
                'rows_deleted': int,
                'overlap_action': dict or None
            }
        """
        # Filter data for this account
        existing_account = existing_df[existing_df['Account Number'] == account].copy()
        new_account = new_df[new_df['Account Number'] == account].copy()

        result = {
            'rows_to_delete': pd.DataFrame(),
            'rows_to_add': pd.DataFrame(),
            'rows_added': 0,
            'rows_deleted': 0,
            'overlap_action': None
        }

        # Case 1: New account (not in existing data)
        if len(existing_account) == 0:
            logging.info(f"Budget   : Account {account}: New account - adding all {len(new_account)} transactions")
            result['rows_to_add'] = new_account.copy()
            result['rows_added'] = len(new_account)
            return result

        # Case 2: Existing account - find last transaction date
        last_date = existing_account['Date'].max()
        logging.info(f"Budget   : Account {account}: Last existing date = {last_date.strftime('%Y-%m-%d')}")

        # Filter new data: >= last_date (inclusive to check for overlap)
        new_filtered = new_account[new_account['Date'] >= last_date].copy()

        if len(new_filtered) == 0:
            logging.info(f"Budget   : Account {account}: No new transactions")
            return result

        # Check for overlap on last_date
        existing_overlap = existing_account[existing_account['Date'] == last_date]
        new_overlap = new_filtered[new_filtered['Date'] == last_date]

        if len(new_overlap) == 0:
            # No overlap - just append all new transactions
            result['rows_to_add'] = new_filtered.copy()
            result['rows_added'] = len(new_filtered)
            logging.info(f"Budget   : Account {account}: No overlap - adding {len(new_filtered)} new transactions")
            return result

        # Overlap detected - analyze transactions
        overlap_result = self.analyze_overlap(
            existing_overlap,
            new_overlap,
            last_date,
            account
        )

        # Apply overlap decision
        if overlap_result['action'] == 'exact_match':
            # Skip overlap date, add transactions after
            result['rows_to_add'] = new_filtered[new_filtered['Date'] > last_date].copy()
            result['rows_added'] = len(result['rows_to_add'])
            logging.info(
                f"Budget   : Account {account}: Exact match on {last_date.strftime('%Y-%m-%d')} "
                f"- adding {result['rows_added']} transactions after overlap"
            )

        elif overlap_result['action'] == 'all_new':
            # All transactions on overlap date are new
            result['rows_to_add'] = new_filtered.copy()
            result['rows_added'] = len(new_filtered)
            logging.info(
                f"Budget   : Account {account}: All new transactions on {last_date.strftime('%Y-%m-%d')} "
                f"- adding {result['rows_added']} transactions"
            )

        elif overlap_result['action'] == 'override':
            # Override: delete old overlap, add all new overlap + future
            result['rows_to_delete'] = existing_overlap.copy()
            result['rows_to_add'] = new_filtered.copy()
            result['rows_deleted'] = len(existing_overlap)
            result['rows_added'] = len(new_filtered)
            logging.warning(
                f"Budget   : Account {account}: Override on {last_date.strftime('%Y-%m-%d')} "
                f"- deleting {result['rows_deleted']} old, adding {result['rows_added']} new"
            )

        result['overlap_action'] = {
            'account': account,
            'date': last_date.strftime('%Y-%m-%d'),
            'action': overlap_result['action'],
            'old_count': len(existing_overlap),
            'new_count': len(new_overlap)
        }

        return result

    def update_excel_budget(self, excel_file=None, sheet_name='Details', backup=True):
        """
        Incrementally update budget.xlsx details sheet with new transaction data.

        This method processes each account separately, detects overlaps, and handles
        three scenarios:
        - Exact match: Skip duplicate transactions
        - All new: Append all transactions
        - Override: Delete old overlap data, insert new (when new_count > old_count)

        Args:
            excel_file (str): Path to budget.xlsx (defaults to BUDGET_DATA_DIR/budget.xlsx)
            sheet_name (str): Sheet name to update (default: 'Details')
            backup (bool): Create timestamped backup before updating (default: True)

        Returns:
            dict: Statistics {
                'accounts_processed': int,
                'accounts_failed': list,
                'rows_added': int,
                'rows_deleted': int,
                'overlap_actions': list,
                'warnings': list
            }
        """
        import shutil

        # Set default Excel file path using environment variable
        if excel_file is None:
            budget_data_dir = os.environ.get('BUDGET_DATA_DIR', './data')
            excel_file = os.path.join(budget_data_dir, 'budget.xlsx')

        logging.info(f"Budget   : Loading existing data from {excel_file}, sheet '{sheet_name}'")

        # Load existing data from Excel
        existing_df = pd.read_excel(excel_file, sheet_name=sheet_name)
        existing_df['Date'] = pd.to_datetime(existing_df['Date'], format='mixed', dayfirst=True)

        logging.info(f"Budget   : Loaded {len(existing_df):,} existing transactions")

        # Prepare new data (self.data is already loaded from preprocessed file)
        self.data['Date'] = pd.to_datetime(self.data['Date'], format='mixed', dayfirst=True)

        logging.info(f"Budget   : Preparing to import {len(self.data):,} new transactions")

        # Backup if enabled
        if backup:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = excel_file.replace('.xlsx', f'_backup_{timestamp}.xlsx')
            shutil.copy2(excel_file, backup_path)
            logging.info(f"Budget   : Backup created: {backup_path}")

            # Clean up old backups (>3 months)
            self.cleanup_old_backups(excel_file, days=90)

        # Initialize statistics
        stats = {
            'accounts_processed': 0,
            'accounts_failed': [],
            'rows_added': 0,
            'rows_deleted': 0,
            'overlap_actions': [],
            'warnings': []
        }

        all_rows_to_delete = []
        all_rows_to_add = []

        # Process each account
        accounts = self.data['Account Number'].unique()

        for account in accounts:
            try:
                # Process single account
                result = self.process_account(
                    account,
                    existing_df,
                    self.data
                )

                # Collect changes
                if len(result['rows_to_delete']) > 0:
                    all_rows_to_delete.append(result['rows_to_delete'])

                if len(result['rows_to_add']) > 0:
                    all_rows_to_add.append(result['rows_to_add'])

                # Update stats
                stats['accounts_processed'] += 1
                stats['rows_added'] += result['rows_added']
                stats['rows_deleted'] += result['rows_deleted']

                if result['overlap_action']:
                    stats['overlap_actions'].append(result['overlap_action'])

            except Exception as e:
                # Log error and continue with other accounts
                error_msg = f"Account {account}: {str(e)}"
                logging.error(f"Budget   : {error_msg}")
                stats['accounts_failed'].append({'account': account, 'error': str(e)})
                continue  # Continue with next account

        # Apply changes to DataFrame
        compare_cols = ['Date', 'Account Number', 'Amount', 'Type', 'Memo']

        # Delete old overlap transactions (if any)
        if all_rows_to_delete:
            delete_df = pd.concat(all_rows_to_delete, ignore_index=True)

            # Create a unique identifier for deletion
            existing_df['_temp_index'] = existing_df.index
            merged = existing_df.merge(
                delete_df[compare_cols],
                on=compare_cols,
                how='inner'
            )
            indices_to_delete = merged['_temp_index'].values

            existing_df = existing_df.drop(indices_to_delete)
            existing_df = existing_df.drop(columns=['_temp_index'])

            logging.info(f"Budget   : Deleted {len(indices_to_delete)} rows from existing data")

        # Append new transactions
        if all_rows_to_add:
            new_df_concat = pd.concat(all_rows_to_add, ignore_index=True)
            updated_df = pd.concat([existing_df, new_df_concat], ignore_index=True)
            logging.info(f"Budget   : Added {len(new_df_concat)} rows to existing data")
        else:
            updated_df = existing_df
            logging.info("Budget   : No new rows to add")

        # Sort by Date, then Account Number
        updated_df = updated_df.sort_values(['Date', 'Account Number']).reset_index(drop=True)

        # Write to Excel using openpyxl engine
        # Note: This preserves other sheets in the workbook
        with pd.ExcelWriter(excel_file, engine='openpyxl', mode='a', if_sheet_exists='replace',
                           date_format='DD/MM/YYYY', datetime_format='DD/MM/YYYY') as writer:
            updated_df.to_excel(writer, sheet_name=sheet_name, index=False)

            # Get the worksheet and format the Date column
            worksheet = writer.sheets[sheet_name]
            from openpyxl.styles import numbers

            # Format column A (Date column) as date without time
            for cell in worksheet['A'][1:]:  # Skip header row
                if cell.value:
                    cell.number_format = 'DD/MM/YYYY'

        logging.info(f"Budget   : Excel file updated: {excel_file}")

        # Print summary
        print(f"\n{'=' * 80}")
        print("IMPORT SUMMARY")
        print(f"{'=' * 80}")
        print(f"Accounts processed: {stats['accounts_processed']}")
        print(f"Accounts failed: {len(stats['accounts_failed'])}")
        print(f"Rows added: {stats['rows_added']}")
        print(f"Rows deleted: {stats['rows_deleted']}")
        print(f"Net change: +{stats['rows_added'] - stats['rows_deleted']} rows")

        if stats['overlap_actions']:
            print(f"\nOverlap Actions:")
            for action in stats['overlap_actions']:
                print(f"  {action['account']:30s} ({action['date']}): {action['action']:15s} "
                      f"(old={action['old_count']}, new={action['new_count']})")

        if stats['accounts_failed']:
            print(f"\nFailed Accounts:")
            for fail in stats['accounts_failed']:
                print(f"  {fail['account']:30s}: {fail['error']}")

        print(f"{'=' * 80}\n")

        self.stats = stats
        return stats

    def cleanup_raw_files(self):
        """
        Remove raw data files after successful processing.

        Deletes:
        - data*.csv (Barclays account exports)
        - activity.csv (Amex raw file, if not already renamed)

        Should only be called after successful update_excel_budget().
        """
        deleted_files = []
        errors = []

        # Delete data*.csv files
        pattern = os.path.join(self.path_to_tmp, "data*.csv")
        data_files = glob.glob(pattern)

        for f in data_files:
            try:
                os.remove(f)
                deleted_files.append(f)
                logging.info(f"Budget   : Deleted raw file: {f}")
            except Exception as e:
                errors.append({'file': f, 'error': str(e)})
                logging.error(f"Budget   : Failed to delete {f}: {e}")

        # Delete activity.csv if it still exists (shouldn't after rename, but just in case)
        activity_file = os.path.join(self.path_to_tmp, "activity.csv")
        if os.path.exists(activity_file):
            try:
                os.remove(activity_file)
                deleted_files.append(activity_file)
                logging.info(f"Budget   : Deleted raw file: {activity_file}")
            except Exception as e:
                errors.append({'file': activity_file, 'error': str(e)})
                logging.error(f"Budget   : Failed to delete {f}: {e}")

        # Log summary
        if deleted_files:
            logging.info(f"Budget   : Cleanup complete - deleted {len(deleted_files)} raw file(s)")
            print(f"Cleanup: Deleted {len(deleted_files)} raw file(s)")
        else:
            logging.info("Budget   : Cleanup - no raw files to delete")
            print("Cleanup: No raw files to delete")

        if errors:
            logging.warning(f"Budget   : Cleanup had {len(errors)} error(s)")
            for err in errors:
                print(f"  Failed to delete: {err['file']}")

        return {'deleted': deleted_files, 'errors': errors}


# MAIN PROGRAM
if __name__ == "__main__":

    # Set up logging directory (use ./logs if it exists, otherwise current directory)
    log_dir = Path("logs") if Path("logs").exists() else Path(".")
    log_file = log_dir / f"{date.today().strftime('%Y%m%d')}report.log"

    format = "%(asctime)s: %(message)s"
    logging.basicConfig(format=format, level=logging.INFO,
                        datefmt="%H:%M:%S", filename=str(log_file))

    logging.info("Main    : initialisation")

    try:
        # arguments
        parser = ap.ArgumentParser()

        parser.add_argument('-date', default=None,help='date of the raw data')

        # parse the arguments
        args = parser.parse_args()
        parsing_date = args.date
        logging.info("Main    : arguments passed - date: {date}".format(date=parsing_date))

        #date = '2021_10_03'

        logging.info("Main    : Creating Budget object")
        data = Budget(parsing_date)
        logging.info(f"Main    : Budget object created - data has {len(data.data)} rows")

        logging.info("Main    : Starting combine_and_rename_files()")
        data.combine_and_rename_files()
        logging.info("Main    : combine_and_rename_files() completed")

        logging.info("Main    : Starting import_raw_data()")
        data.import_raw_data()
        logging.info(f"Main    : import_raw_data() completed - data has {len(data.data)} rows")

        logging.info("Main    : Starting export_preprocessed_data()")
        data.export_preprocessed_data()
        logging.info("Main    : export_preprocessed_data() completed")

        logging.info("Main    : Starting update_excel_budget()")
        stats = data.update_excel_budget()
        logging.info("Main    : update_excel_budget() completed")

        logging.info(f"Main    : Complete - Added {stats['rows_added']} rows, deleted {stats['rows_deleted']} rows")

        # Cleanup raw files only if processing was successful (no failed accounts)
        if len(stats['accounts_failed']) == 0:
            logging.info("Main    : Starting cleanup_raw_files()")
            data.cleanup_raw_files()
            logging.info("Main    : cleanup_raw_files() completed")
        else:
            logging.warning("Main    : Skipping cleanup due to failed accounts")
            print("Warning: Skipping raw file cleanup due to failed accounts")

        logging.info("Main    : Process completed successfully")

    except Exception as e:
        logging.error(f"Main    : FATAL ERROR - {type(e).__name__}: {e}")
        logging.error("Main    : Traceback follows:")
        import traceback
        for line in traceback.format_exc().split('\n'):
            if line:
                logging.error(f"Main    : {line}")
        print(f"\n{'=' * 80}")
        print("FATAL ERROR")
        print(f"{'=' * 80}")
        print(f"Error: {type(e).__name__}: {e}")
        print(f"\nSee log file for details: {log_file}")
        print(f"{'=' * 80}\n")
        raise
