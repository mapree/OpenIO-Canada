"""
Class that creates symmetric Input-Output tables based on the Supply and Use economic tables provided by Statistic
Canada, available here: https://www150.statcan.gc.ca/n1/en/catalogue/15-602-X
Multiple transformation models are available (industry technology assumption, fixed industry sales structure, etc.) and
the type of classification (productxproduct, or industryxindustry) can be selected as well.
Also produces environmental extensions for the symmetric tables generated based on data from the NPRI found here:
https://open.canada.ca/data/en/dataset/1fb7d8d4-7713-4ec6-b957-4a882a84fed3
"""

import pandas as pd
import numpy as np
import re
import pkg_resources
import os
import pymrio
import json
import country_converter as coco
import logging
import warnings


class IOTables:
    def __init__(self, folder_path, classification='product', exiobase_folder=None, final_demand_aggregated=True):
        """
        :param folder_path: [string] the path to the folder with the economic data (e.g. /../Detail level/)
        :param classification: [string] the type of classification to adopt for the symmetric IOT ("product" or "industry")
        :param exiobase_folder: [string] path to exiobase folder for international imports (optional)
        """

        # ignoring some warnings
        warnings.filterwarnings(action='ignore', category=FutureWarning)
        warnings.filterwarnings(action='ignore', category=np.VisibleDeprecationWarning)
        warnings.filterwarnings(action='ignore', category=pd.errors.PerformanceWarning)

        # set up logging tool
        logger = logging.getLogger('openIO-Canada')
        logger.setLevel(logging.INFO)
        logger.handlers = []
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
        logger.propagate = False

        logger.info('Reading all the Excel files...')

        self.level_of_detail = [i for i in folder_path.split('/') if 'level' in i][0]
        self.classification = classification
        self.exiobase_folder = exiobase_folder
        self.final_demand_aggregated = final_demand_aggregated

        if self.classification == "product":
            self.assumption = 'industry technology'
        elif self.classification == "industry":
            self.assumption = 'fixed industry sales structure'

        # values
        self.V = pd.DataFrame()
        self.U = pd.DataFrame()
        self.A = pd.DataFrame()
        self.Z = pd.DataFrame()
        self.W = pd.DataFrame()
        self.R = pd.DataFrame()
        self.Y = pd.DataFrame()
        self.WY = pd.DataFrame()
        self.g = pd.DataFrame()
        self.inv_g = pd.DataFrame()
        self.q = pd.DataFrame()
        self.inv_q = pd.DataFrame()
        self.F = pd.DataFrame()
        self.S = pd.DataFrame()
        self.FY = pd.DataFrame()
        self.C = pd.DataFrame()
        self.INT_imports = pd.DataFrame()
        self.L = pd.DataFrame()
        self.E = pd.DataFrame()
        self.D = pd.DataFrame()
        self.who_uses_int_imports = pd.DataFrame()
        self.A_exio = pd.DataFrame()
        self.S_exio = pd.DataFrame()
        self.F_exio = pd.DataFrame()
        self.C_exio = pd.DataFrame()
        self.link_openio_exio_technosphere = pd.DataFrame()
        self.link_openio_exio_final_demands = pd.DataFrame()
        self.merchandise_imports = pd.DataFrame()
        self.merchandise_imports_scaled = pd.DataFrame()
        self.minerals = pd.DataFrame()

        # metadata
        self.emission_metadata = pd.DataFrame()
        self.unit_exio = pd.DataFrame()
        self.methods_metadata = pd.DataFrame()
        self.industries = []
        self.commodities = []
        self.factors_of_production = []

        self.matching_dict = {'AB': 'Alberta',
                                'BC': 'British Columbia',
                                'MB': 'Manitoba',
                                'NB': 'New Brunswick',
                                'NL': 'Newfoundland and Labrador',
                                'NS': 'Nova Scotia',
                                'NT': 'Northwest Territories',
                                'NU': 'Nunavut',
                                'ON': 'Ontario',
                                'PE': 'Prince Edward Island',
                                'QC': 'Quebec',
                                'SK': 'Saskatchewan',
                                'YT': 'Yukon'}

        files = [i for i in os.walk(folder_path)]
        files = [i for i in files[0][2] if i[:2] in self.matching_dict.keys() and 'SUT' in i]
        self.year = int(files[0].split('SUT_C')[1].split('_')[0])

        try:
            self.NPRI = pd.read_excel(pkg_resources.resource_stream(
                __name__, '/Data/Environmental_data/NPRI-INRP_DataDonnées_' + str(self.year) + '.xlsx'), None)
            self.NPRI_file_year = self.year
        # 2016 by default (for older years)
        except FileNotFoundError:
            self.NPRI = pd.read_excel(pkg_resources.resource_stream(
                __name__, '/Data/Environmental_data/NPRI-INRP_DataDonnées_2016.xlsx'), None)
            self.NPRI_file_year = 2016

        logger.info("Formatting the Supply and Use tables...")
        for province_data in files:
            su_tables = pd.read_excel(folder_path+province_data, None)
            region = province_data[:2]
            self.format_tables(su_tables, region)

        self.W = self.W.fillna(0)
        self.WY = self.WY.fillna(0)
        self.Y = self.Y.fillna(0)
        self.q = self.q.fillna(0)
        self.g = self.g.fillna(0)
        self.U = self.U.fillna(0)
        self.V = self.V.fillna(0)

        logger.info("Modifying names of duplicated sectors...")
        self.dealing_with_duplicated_names()

        if self.final_demand_aggregated:
            logger.info('Aggregating final demand sectors...')
            self.aggregate_final_demand()
        else:
            logger.info('Organizing final demand sectors...')
            self.organize_final_demand()

        logger.info('Removing IOIC codes from index...')
        self.remove_codes()

        logger.info("Balancing inter-provincial trade...")
        self.province_import_export(
            pd.read_excel(
                folder_path+[i for i in [j for j in os.walk(folder_path)][0][2] if 'Provincial_trade_flow' in i][0],
                'Data'))

        if self.exiobase_folder:
            logger.info('Pre-treatment of international trade data...')
            self.determine_sectors_importing()
            self.load_merchandise_international_trade_database()
            logger.info("Linking international trade data to openIO-Canada...")
            self.link_merchandise_database_to_openio()

        logger.info("Building the symmetric tables...")
        self.gimme_symmetric_iot()

        if self.exiobase_folder:
            logger.info("Linking openIO-Canada to Exiobase...")
            self.link_international_trade_data_to_exiobase()
            self.concatenate_matrices()

        logger.info("Extracting and formatting environmental data from the NPRI file...")
        self.extract_environmental_data()

        logger.info("Matching emission data from NPRI to IOT sectors...")
        self.match_npri_data_to_iots()

        logger.info("Matching GHG accounts to IOT sectors...")
        self.match_ghg_accounts_to_iots()

        logger.info("Matching water accounts to IOT sectors...")
        self.match_water_accounts_to_iots()

        logger.info("Matching energy accounts to IOT sectors...")
        self.match_energy_accounts_to_iots()

        logger.info("Matching mineral extraction data to IOT sectors...")
        self.match_mineral_extraction_to_iots()

        logger.info("Creating the characterization matrix...")
        self.characterization_matrix()

        logger.info("Refining the GHG emissions for the agriculture sector...")
        self.better_distribution_for_agriculture_ghgs()

        logger.info("Cleaning province and country names...")
        self.differentiate_country_names_openio_exio()

        logger.info("Refining the GHG emissions for the meat sector...")
        self.refine_meat_sector()

        logger.info("Normalizing emissions...")
        self.normalize_flows()

        logger.info("Differentiating biogenic from fossil CO2 emissions...")
        self.differentiate_biogenic_carbon_emissions()

        logger.info("Done extracting openIO-Canada!")

    def format_tables(self, su_tables, region):
        """
        Extracts the relevant dataframes from the Excel files in the Stat Can folder
        :param su_tables: the supply and use economic tables
        :param region: the province of Canada to compile data for
        :return: self.W, self.WY, self.Y, self.g, self.q, self.V, self.U
        """

        supply_table = su_tables['Supply'].copy()
        use_table = su_tables['Use_Basic'].copy()

        if self.year in [2014, 2015, 2016, 2017]:
            # starting_line is the line in which the Supply table starts (the first green row)
            starting_line = 11
            # starting_line_values is the line in which the first value appears
            starting_line_values = 16

        elif self.year in [2018, 2019]:
            # starting_line is the line in which the Supply table starts (the first green row)
            starting_line = 3
            # starting_line_values is the line in which the first value appears
            starting_line_values = 7

        if not self.industries:
            for i in range(0, len(supply_table.columns)):
                if supply_table.iloc[starting_line, i] == 'Total':
                    break
                if supply_table.iloc[starting_line, i] not in [np.nan, 'Industries']:
                    # tuple with code + name (need code to deal with duplicate names in detailed levels)
                    self.industries.append((supply_table.iloc[starting_line+1, i],
                                            supply_table.iloc[starting_line, i]))
            # remove fictive sectors
            self.industries = [i for i in self.industries if not re.search(r'^F', i[0])]

        if not self.commodities:
            for i, element in enumerate(supply_table.iloc[:, 0].tolist()):
                if type(element) == str:
                    # identify by their codes
                    if re.search(r'^[M,F,N,G,I,E]\w*\d', element):
                        self.commodities.append((element, supply_table.iloc[i, 1]))
                    elif re.search(r'^P\w*\d', element) or re.search(r'^GVA', element):
                        self.factors_of_production.append((element, supply_table.iloc[i, 1]))

        final_demand = []
        for i in range(0, len(use_table.columns)):
            if use_table.iloc[starting_line, i] == 'Total use':
                break
            if use_table.iloc[starting_line, i] not in [np.nan, 'Industries']:
                final_demand.append((use_table.iloc[starting_line+1, i],
                                     use_table.iloc[starting_line, i]))
        final_demand = [i for i in final_demand if i not in self.industries and i[1] != 'Total']

        df = supply_table.iloc[starting_line_values-2:, 2:]
        df.index = list(zip(supply_table.iloc[starting_line_values-2:, 0].tolist(),
                            supply_table.iloc[starting_line_values-2:, 1].tolist()))
        df.columns = list(zip(supply_table.iloc[starting_line+1, 2:].tolist(),
                              supply_table.iloc[starting_line, 2:].tolist()))
        supply_table = df

        df = use_table.iloc[starting_line_values-2:, 2:]
        df.index = list(zip(use_table.iloc[starting_line_values-2:, 0].tolist(),
                            use_table.iloc[starting_line_values-2:, 1].tolist()))
        df.columns = list(zip(use_table.iloc[starting_line+1, 2:].tolist(),
                              use_table.iloc[starting_line, 2:].tolist()))
        use_table = df

        # fill with zeros
        supply_table.replace('.', 0, inplace=True)
        use_table.replace('.', 0, inplace=True)

        # get strings as floats
        supply_table = supply_table.astype('float64')
        use_table = use_table.astype('float64')

        if self.level_of_detail == 'Detail level':
            # tables from k$ to $
            supply_table *= 1000
            use_table *= 1000
        else:
            # tables from M$ to $
            supply_table *= 1000000
            use_table *= 1000000

        # check calculated totals matched displayed totals
        assert np.allclose(use_table.iloc[:, use_table.columns.get_loc(('TOTAL', 'Total'))],
                           use_table.iloc[:, :use_table.columns.get_loc(('TOTAL', 'Total'))].sum(axis=1), atol=1e-5)
        assert np.allclose(supply_table.iloc[supply_table.index.get_loc(('TOTAL', 'Total'))],
                           supply_table.iloc[:supply_table.index.get_loc(('TOTAL', 'Total'))].sum(), atol=1e-5)

        # extract the tables we need
        W = use_table.loc[self.factors_of_production, self.industries]
        W.drop(('GVA', 'Gross value-added at basic prices'), inplace=True)
        Y = use_table.loc[self.commodities, final_demand]
        WY = use_table.loc[self.factors_of_production, final_demand]
        WY.drop(('GVA', 'Gross value-added at basic prices'), inplace=True)
        g = use_table.loc[[('TOTAL', 'Total')], self.industries]
        q = supply_table.loc[self.commodities, [('TOTAL', 'Total')]]
        V = supply_table.loc[self.commodities, self.industries]
        U = use_table.loc[self.commodities, self.industries]
        INT_imports = supply_table.loc[self.commodities,
                                       [i for i in supply_table.columns if re.search(r'^INTIM', i[0])]]

        # create multiindex with region as first level
        for matrix in [W, Y, WY, g, q, V, U, INT_imports]:
            matrix.columns = pd.MultiIndex.from_product([[region], matrix.columns]).tolist()
            matrix.index = pd.MultiIndex.from_product([[region], matrix.index]).tolist()

        # concat the region tables with the all the other tables
        self.W = pd.concat([self.W, W])
        self.WY = pd.concat([self.WY, WY])
        self.Y = pd.concat([self.Y, Y])
        self.q = pd.concat([self.q, q])
        self.g = pd.concat([self.g, g])
        self.U = pd.concat([self.U, U])
        self.V = pd.concat([self.V, V])
        self.INT_imports = pd.concat([self.INT_imports, INT_imports])

        # assert np.isclose(self.V.sum().sum(), self.g.sum().sum())
        # assert np.isclose(self.U.sum().sum()+self.Y.drop([
        #     i for i in self.Y.columns if i[1] == ('IPTEX', 'Interprovincial exports')], axis=1).sum().sum(),
        #                   self.q.sum().sum())

    def dealing_with_duplicated_names(self):
        """
        IOIC classification has duplicate names, so we rename when it's the case
        :return: updated dataframes
        """

        # reindexing to fix the order of the columns
        self.V = self.V.T.reindex(pd.MultiIndex.from_product([self.matching_dict, self.industries]).tolist()).T
        self.U = self.U.T.reindex(pd.MultiIndex.from_product([self.matching_dict, self.industries]).tolist()).T
        self.g = self.g.T.reindex(pd.MultiIndex.from_product([self.matching_dict, self.industries]).tolist()).T
        self.W = self.W.T.reindex(pd.MultiIndex.from_product([self.matching_dict, self.industries]).tolist()).T

        if self.level_of_detail in ['Link-1961 level', 'Link-1997 level', 'Detail level']:
            self.industries = [(i[0], i[1] + ' (private)') if re.search(r'^BS61', i[0]) else i for i in
                               self.industries]
            self.industries = [(i[0], i[1] + ' (non-profit)') if re.search(r'^NP61|^NP71', i[0]) else i for i in
                               self.industries]
            self.industries = [(i[0], i[1] + ' (public)') if re.search(r'^GS61', i[0]) else i for i in
                               self.industries]
        if self.level_of_detail in ['Link-1997 level', 'Detail level']:
            self.industries = [(i[0], i[1] + ' (private)') if re.search(r'^BS623|^BS624', i[0]) else i for i in
                               self.industries]
            self.industries = [(i[0], i[1] + ' (non-profit)') if re.search(r'^NP624', i[0]) else i for i in
                               self.industries]
            self.industries = [(i[0], i[1] + ' (public)') if re.search(r'^GS623', i[0]) else i for i in
                               self.industries]

        # applying the change of names to columns
        for df in [self.V, self.U, self.g, self.W]:
            df.columns = pd.MultiIndex.from_product([self.matching_dict, self.industries]).tolist()

    def aggregate_final_demand(self):
        """
        Aggregates all final demand sectors into 6 elements: ["Household final consumption expenditure",
        "Non-profit institutions serving households' final consumption expenditure",
        "Governments final consumption expenditure", "Gross fixed capital formation", "Changes in inventories",
        "International exports"]
        Provincial exports will be included in self.U and are thus excluded from self.Y
        :return: self.Y with final demand sectors aggregated
        """

        # final demands are identified through their codes, hence the use of regex
        aggregated_Y = self.Y.loc[:, [i for i in self.Y.columns if
                                      re.search(r'^PEC\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        aggregated_Y.columns = pd.MultiIndex.from_product([aggregated_Y.columns,
                                                           ["Household final consumption expenditure"]])

        df = self.Y.loc[:, [i for i in self.Y.columns if
                            re.search(r'^CEN\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Non-profit institutions serving households' final consumption expenditure"]])
        aggregated_Y = pd.concat([aggregated_Y, df], axis=1)

        df = self.Y.loc[:, [i for i in self.Y.columns if
                            re.search(r'^CEG\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Governments final consumption expenditure"]])
        aggregated_Y = pd.concat([aggregated_Y, df], axis=1)

        df = self.Y.loc[:, [i for i in self.Y.columns if
                            re.search(r'^CO\w*\d|^ME\w*\d|^IP\w[T]*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Gross fixed capital formation"]])
        aggregated_Y = pd.concat([aggregated_Y, df], axis=1)

        df = self.Y.loc[:, [i for i in self.Y.columns if
                            re.search(r'^INV\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Changes in inventories"]])
        aggregated_Y = pd.concat([aggregated_Y, df], axis=1)

        df = self.Y.loc[:, [i for i in self.Y.columns if
                            re.search(r'^INT\w*', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["International exports"]])
        aggregated_Y = pd.concat([aggregated_Y, df], axis=1)

        self.Y = aggregated_Y
        self.Y = self.Y.T.sort_index().T

        aggregated_WY = self.WY.loc[:, [i for i in self.WY.columns if
                                      re.search(r'^PEC\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        aggregated_WY.columns = pd.MultiIndex.from_product([aggregated_WY.columns,
                                                           ["Household final consumption expenditure"]])

        df = self.WY.loc[:, [i for i in self.WY.columns if
                            re.search(r'^CEN\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Non-profit institutions serving households' final consumption expenditure"]])
        aggregated_WY = pd.concat([aggregated_WY, df], axis=1)

        df = self.WY.loc[:, [i for i in self.WY.columns if
                            re.search(r'^CEG\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Governments final consumption expenditure"]])
        aggregated_WY = pd.concat([aggregated_WY, df], axis=1)

        df = self.WY.loc[:, [i for i in self.WY.columns if
                            re.search(r'^CO\w*\d|^ME\w*\d|^IP\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Gross fixed capital formation"]])
        aggregated_WY = pd.concat([aggregated_WY, df], axis=1)

        df = self.WY.loc[:, [i for i in self.WY.columns if
                            re.search(r'^INV\w*\d', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["Changes in inventories"]])
        aggregated_WY = pd.concat([aggregated_WY, df], axis=1)

        df = self.WY.loc[:, [i for i in self.WY.columns if
                            re.search(r'^INT\w*', i[1][0])]].groupby(level=0, axis=1).sum()
        df.columns = pd.MultiIndex.from_product([
            df.columns, ["International exports"]])
        aggregated_WY = pd.concat([aggregated_WY, df], axis=1)

        self.WY = aggregated_WY
        self.WY = self.WY.T.sort_index().T

        for df in [self.Y, self.WY]:
            assert len([i for i in df.columns.levels[1] if i not in [
                "Household final consumption expenditure",
                "Non-profit institutions serving households' final consumption expenditure",
                "Governments final consumption expenditure",
                "Gross fixed capital formation",
                "Changes in inventories",
                "International exports"
            ]]) == 0

    def organize_final_demand(self):
        """
        Extract the final demand sectors. These will be disaggregated. If you do not want the detail, use
        self.aggregated_final_demand()
        Provincial exports will be included in self.U and are thus excluded from self.Y
        :return: self.Y & self.WY updated
        """

        # dealing with duplicate names of disaggregated final demand sector names separately
        self.Y.columns = [(i[0], (i[1][0], i[1][1] + ' (private)')) if re.search(r'^COB61|^MEB61|^IPB61|^MEBU', i[1][0])
                          else i for i in self.Y.columns]
        self.Y.columns = [(i[0], (i[1][0], i[1][1] + ' (public)')) if re.search(r'^COG61|^MEG61|^IPG61|^MEGU', i[1][0])
                          else i for i in self.Y.columns]
        self.Y.columns = [(i[0], (i[1][0], i[1][1] + ' (non-profit)')) if re.search(r'^MENU', i[1][0])
                          else i for i in self.Y.columns]
        self.WY.columns = [(i[0], (i[1][0], i[1][1] + ' (private)')) if re.search(r'^COB61|^MEB61|^IPB61|^MEBU', i[1][0])
                          else i for i in self.WY.columns]
        self.WY.columns = [(i[0], (i[1][0], i[1][1] + ' (public)')) if re.search(r'^COG61|^MEG61|^IPG61|^MEGU', i[1][0])
                          else i for i in self.WY.columns]
        self.WY.columns = [(i[0], (i[1][0], i[1][1] + ' (non-profit)')) if re.search(r'^MENU', i[1][0])
                          else i for i in self.WY.columns]

        Y = pd.DataFrame()

        fd_households = [i for i in self.Y.columns if re.search(r'^PEC\w*\d', i[1][0])]
        df = self.Y.loc[:, fd_households].copy()
        df.columns = [(i[0], "Household final consumption expenditure", i[1][1]) for i in fd_households]
        Y = pd.concat([Y, df], axis=1)

        fd_npish = [i for i in self.Y.columns if re.search(r'^CEN\w*\d', i[1][0])]
        df = self.Y.loc[:, fd_npish].copy()
        df.columns = [(i[0], i[1][1], 'NPISH') for i in fd_npish]
        Y = pd.concat([Y, df], axis=1)

        fd_gov = [i for i in self.Y.columns if re.search(r'^CEG\w*\d', i[1][0])]
        df = self.Y.loc[:, fd_gov].copy()
        df.columns = [(i[0], "Governments final consumption expenditure", i[1][1]) for i in fd_gov]
        Y = pd.concat([Y, df], axis=1)

        fd_construction = [i for i in self.Y.columns if re.search(r'^CO\w*\d', i[1][0])]
        df = self.Y.loc[:, fd_construction].copy()
        df.columns = [(i[0], "Gross fixed capital formation, Construction", i[1][1]) for i in fd_construction]
        Y = pd.concat([Y, df], axis=1)

        fd_machinery = [i for i in self.Y.columns if re.search(r'^ME\w*\d', i[1][0])]
        df = self.Y.loc[:, fd_machinery].copy()
        df.columns = [(i[0], "Gross fixed capital formation, Machinery and equipment", i[1][1]) for i in fd_machinery]
        Y = pd.concat([Y, df], axis=1)

        fd_ip = [i for i in self.Y.columns if re.search(r'^IP\w[T]*\d', i[1][0])]
        df = self.Y.loc[:, fd_ip].copy()
        df.columns = [(i[0], "Gross fixed capital formation, Intellectual property products", i[1][1]) for i in fd_ip]
        Y = pd.concat([Y, df], axis=1)

        fd_inv = [i for i in self.Y.columns if re.search(r'^INV\w*\d', i[1][0])]
        df = self.Y.loc[:, fd_inv].copy()
        df.columns = [(i[0], "Changes in inventories", i[1][1]) for i in fd_inv]
        Y = pd.concat([Y, df], axis=1)

        fd_int = [i for i in self.Y.columns if re.search(r'^INT\w*', i[1][0])]
        df = self.Y.loc[:, fd_int].copy()
        df.columns = [(i[0], "International exports", i[1][1].split(' ')[1].capitalize()) for i in fd_int]
        Y = pd.concat([Y, df], axis=1)

        self.Y = Y

        WY = pd.DataFrame()

        fd_households = [i for i in self.WY.columns if re.search(r'^PEC\w*\d', i[1][0])]
        df = self.WY.loc[:, fd_households].copy()
        df.columns = [(i[0], "Household final consumption expenditure", i[1][1]) for i in fd_households]
        WY = pd.concat([WY, df], axis=1)

        fd_npish = [i for i in self.WY.columns if re.search(r'^CEN\w*\d', i[1][0])]
        df = self.WY.loc[:, fd_npish].copy()
        df.columns = [(i[0], i[1][1], 'NPISH') for i in fd_npish]
        WY = pd.concat([WY, df], axis=1)

        fd_gov = [i for i in self.WY.columns if re.search(r'^CEG\w*\d', i[1][0])]
        df = self.WY.loc[:, fd_gov].copy()
        df.columns = [(i[0], "Governments final consumption expenditure", i[1][1]) for i in fd_gov]
        WY = pd.concat([WY, df], axis=1)

        fd_construction = [i for i in self.WY.columns if re.search(r'^CO\w*\d', i[1][0])]
        df = self.WY.loc[:, fd_construction].copy()
        df.columns = [(i[0], "Gross fixed capital formation, Construction", i[1][1]) for i in fd_construction]
        WY = pd.concat([WY, df], axis=1)

        fd_machinery = [i for i in self.WY.columns if re.search(r'^ME\w*\d', i[1][0])]
        df = self.WY.loc[:, fd_machinery].copy()
        df.columns = [(i[0], "Gross fixed capital formation, Machinery and equipment", i[1][1]) for i in fd_machinery]
        WY = pd.concat([WY, df], axis=1)

        fd_ip = [i for i in self.WY.columns if re.search(r'^IP\w[T]*\d', i[1][0])]
        df = self.WY.loc[:, fd_ip].copy()
        df.columns = [(i[0], "Gross fixed capital formation, Intellectual property products", i[1][1]) for i in fd_ip]
        WY = pd.concat([WY, df], axis=1)

        fd_inv = [i for i in self.WY.columns if re.search(r'^INV\w*\d', i[1][0])]
        df = self.WY.loc[:, fd_inv].copy()
        df.columns = [(i[0], "Changes in inventories", i[1][1]) for i in fd_inv]
        WY = pd.concat([WY, df], axis=1)

        fd_int = [i for i in self.WY.columns if re.search(r'^INT\w*', i[1][0])]
        df = self.WY.loc[:, fd_int].copy()
        df.columns = [(i[0], "International exports", i[1][1].split(' ')[1].capitalize()) for i in fd_int]
        WY = pd.concat([WY, df], axis=1)

        self.WY = WY

    def remove_codes(self):
        """
        Removes the IOIC codes from the index to only leave the name.
        :return: Dataframes with the code of the multi-index removed
        """
        # removing the IOIC codes
        for df in [self.W, self.g, self.V, self.U, self.INT_imports]:
            df.columns = [(i[0], i[1][1]) for i in df.columns]
        for df in [self.W, self.Y, self.WY, self.q, self.V, self.U, self.INT_imports]:
            df.index = [(i[0], i[1][1]) for i in df.index]

        # recreating MultiIndexes
        for df in [self.W, self.Y, self.WY, self.g, self.q, self.V, self.U, self.INT_imports]:
            df.index = pd.MultiIndex.from_tuples(df.index)
            df.columns = pd.MultiIndex.from_tuples(df.columns)

        # reordering columns
        reindexed_columns = pd.MultiIndex.from_product([list(self.matching_dict.keys()),
                                                        [i[1] for i in self.industries]])
        self.W = self.W.T.reindex(reindexed_columns).T
        self.g = self.g.T.reindex(reindexed_columns).T
        self.V = self.V.T.reindex(reindexed_columns).T
        self.U = self.U.T.reindex(reindexed_columns).T

    def province_import_export(self, province_trade_file):
        """
        Method extracting and formatting inter province imports/exports
        :return: modified self.U, self.V, self.W, self.Y
        """

        province_trade_file = province_trade_file

        province_trade_file.Origin = [{v: k for k, v in self.matching_dict.items()}[i.split(') ')[1]] if (
                ')' in i and i != '(81) Canadian territorial enclaves abroad') else i for i in
                                      province_trade_file.Origin]
        province_trade_file.Destination = [{v: k for k, v in self.matching_dict.items()}[i.split(') ')[1]] if (
                ')' in i and i != '(81) Canadian territorial enclaves abroad') else i for i in
                                           province_trade_file.Destination]
        # extracting and formatting supply for each province
        province_trade = pd.pivot_table(data=province_trade_file, index='Destination', columns=['Origin', 'Product'])

        province_trade = province_trade.loc[
            [i for i in province_trade.index if i in self.matching_dict], [i for i in province_trade.columns if
                                                                           i[1] in self.matching_dict]]
        if self.level_of_detail == 'Detail level':
            province_trade *= 1000
        else:
            province_trade *= 1000000
        province_trade.columns = [(i[1], i[2].split(': ')[1]) if ':' in i[2] else i for i in
                                  province_trade.columns]
        province_trade.drop([i for i in province_trade.columns if i[1] not in [i[1] for i in self.commodities]],
                            axis=1, inplace=True)
        province_trade.columns = pd.MultiIndex.from_tuples(province_trade.columns)
        for province in province_trade.index:
            province_trade.loc[province, province] = 0

        import_markets = pd.DataFrame(0, province_trade.index, province_trade.columns)
        for importing_province in province_trade.index:
            for exported_product in province_trade.columns.levels[1]:
                import_markets.loc[
                    importing_province, [i for i in import_markets.columns if i[1] == exported_product]] = (
                            province_trade.loc[
                                importing_province, [i for i in province_trade.columns if i[1] == exported_product]] /
                            province_trade.loc[importing_province, [i for i in province_trade.columns if
                                                                    i[1] == exported_product]].sum()).values

        for importing_province in province_trade.index:
            U_Y = pd.concat([self.U.loc[importing_province, importing_province],
                             self.Y.loc[importing_province, importing_province]], axis=1)
            # negative values represent sells, so it does not make sense to rebalance imports with them
            U_Y = U_Y[U_Y > 0].fillna(0)
            total_imports = province_trade.groupby(level=1, axis=1).sum().loc[importing_province]
            index_commodity = [i[1] for i in self.commodities]
            total_imports = total_imports.reindex(index_commodity).fillna(0)
            import_distribution = ((U_Y.T / (U_Y.sum(axis=1))) * total_imports).T.fillna(0)

            # distribution balance imports to the different exporting regions
            final_demand_imports = [i for i in import_distribution.columns if i not in self.U.columns.levels[1]]
            for exporting_province in province_trade.index:
                if importing_province != exporting_province:
                    df = (
                        (import_distribution.T * import_markets.fillna(0).loc[
                            importing_province, exporting_province]).T
                    ).reindex(import_distribution.index).fillna(0)
                    # assert index and columns are the same before using .values
                    assert all(self.U.loc[exporting_province, importing_province].index == df.loc[:,
                                                                                           self.U.columns.levels[
                                                                                               1]].reindex(
                        self.U.loc[exporting_province, importing_province].columns, axis=1).index)
                    assert all(self.U.loc[exporting_province, importing_province].columns == df.loc[:,
                                                                                             self.U.columns.levels[
                                                                                                 1]].reindex(
                        self.U.loc[exporting_province, importing_province].columns, axis=1).columns)
                    # assign new values into self.U and self.Y
                    self.U.loc[exporting_province, importing_province] = df.loc[:,
                                                                         self.U.columns.levels[1]].reindex(
                        self.U.loc[exporting_province, importing_province].columns, axis=1).values
                    if self.final_demand_aggregated:
                        self.Y.loc[exporting_province, importing_province].update(df.loc[:, final_demand_imports])
                    else:
                        # special data treatment if final demand sectors are disaggregated
                        dff = df.loc[:, final_demand_imports]
                        dff.columns = pd.MultiIndex.from_tuples(dff.columns)
                        dff = pd.concat([dff], keys=[importing_province], axis=1)
                        dff = pd.concat([dff], keys=[exporting_province], axis=0)
                        self.Y.update(dff)

            # remove interprovincial from intraprovincial to not double count
            self.U.loc[importing_province, importing_province].update(
                self.U.loc[importing_province, importing_province] - self.U.loc[
                    [i for i in self.matching_dict if i != importing_province], importing_province].groupby(
                    level=1).sum())
            if self.final_demand_aggregated:
                self.Y.loc[importing_province, importing_province].update(
                    self.Y.loc[importing_province, importing_province] - self.Y.loc[
                        [i for i in self.matching_dict if i != importing_province], importing_province].groupby(
                        level=1).sum())
            else:
                df = self.Y.loc[importing_province, importing_province] - self.Y.loc[
                    [i for i in self.matching_dict if i != importing_province], importing_province].groupby(level=1).sum()
                df = pd.concat([df], keys=[importing_province], axis=1)
                df = pd.concat([df], keys=[importing_province], axis=0)
                self.Y.update(df)

            # if some province buys more than they use, drop the value in "changes in inventories"
            # if it occurs, it's probably linked to the immediate re-export to other provinces
            if not len(self.U[self.U > -1].dropna()) == len(self.U):
                print("Warning! Some province bought more than they used.")
                product_creating_issue_index = self.U[self.U < -1].dropna(how='all').dropna(axis=1).index
                product_creating_issue_column = self.U[self.U < -1].dropna(how='all').dropna(axis=1).columns
                value_to_balance = self.U[self.U < -1].dropna(how='all').dropna(axis=1).iloc[0, 0]
                self.U.loc[product_creating_issue_index, product_creating_issue_column] = 0
                self.Y.loc[product_creating_issue_index, (product_creating_issue_index[0][0],
                                                          'Changes in inventories')] += - value_to_balance
            # removing negative values lower than 1$ (potential calculation artefacts)
            self.U = self.U[self.U > 0].fillna(0)
            # checking negative values were removed
            assert not self.U[self.U < 0].any().any()

    def determine_sectors_importing(self):
        """
        Determine which sectors use international imports and removing international imports from use
        :return:
        """

        # aggregating international imports in 1 column
        self.INT_imports = self.INT_imports.groupby(axis=1, level=1).sum()
        # need to flatten multiindex for the concatenation to work properly
        self.Y.columns = self.Y.columns.tolist()
        self.U.columns = self.U.columns.tolist()
        # concat U and Y to look at all users (industry + final demand)
        U_Y = pd.concat([self.U, self.Y], axis=1)
        # negative values represent sells, so it does not make sense to rebalance imports with them
        U_Y = U_Y[U_Y > 0].fillna(0)
        # weighted average of who is requiring the international imports, based on national use
        self.who_uses_int_imports = (U_Y.T / U_Y.sum(1)).T * self.INT_imports.values
        # remove international imports from national use
        self.U = self.U - self.who_uses_int_imports.reindex(self.U.columns, axis=1)
        # check that nothing fuzzy is happening with negative values that are not due to artefacts
        assert len(self.U[self.U < -1].dropna(how='all', axis=1).dropna(how='all', axis=0)) == 0
        # remove negative artefacts (like 1e-10$)
        self.U = self.U[self.U > 0].fillna(0)
        assert not self.U[self.U < 0].any().any()
        # remove international imports from final demand
        self.Y = self.Y - self.who_uses_int_imports.reindex(self.Y.columns, axis=1)
        # remove negative artefacts
        self.Y = pd.concat([self.Y[self.Y >= 0].fillna(0), self.Y[self.Y < -1].fillna(0)], axis=1)
        self.Y = self.Y.groupby(by=self.Y.columns, axis=1).sum()
        self.Y.columns = pd.MultiIndex.from_tuples(self.Y.columns)

    def load_merchandise_international_trade_database(self):
        """
        Loading and treating the international trade merchandise database of Statistics Canada.
        Original source: https://open.canada.ca/data/en/dataset/b1126a07-fd85-4d56-8395-143aba1747a4
        :return:
        """

        # load concordance between HS classification and IOIC classification
        conc = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/HS-IOIC.xlsx'))

        # load database
        merchandise_database = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Imports_data/Imports_' +
                                                                           str(self.year) + '_HS06_treated.xlsx'))
        merchandise_database = merchandise_database.ffill()
        merchandise_database.columns = ['Country', 'HS6', 'Value']

        # apply concordance
        merchandise_database = merchandise_database.merge(conc, on='HS6', how='left')

        # only keep useful information
        merchandise_database = merchandise_database.loc[:, ['IOIC', 'Country', 'Value']]

        # remove HS sectors that cant be matched to IOIC (identified with "None")
        merchandise_database = merchandise_database.drop(
            [i for i in merchandise_database.index if merchandise_database.loc[i, 'IOIC'] == 'None'])

        # change IOIC codes to sector names
        code_to_name = {j[0]: j[1] for j in self.commodities}
        merchandise_database.IOIC = [code_to_name[i] for i in merchandise_database.IOIC]

        # set MultiIndex with country and classification
        merchandise_database = merchandise_database.set_index(['Country', 'IOIC'])

        # regroup purchases together (on country + IOIC sector)
        merchandise_database = merchandise_database.groupby(merchandise_database.index).sum()

        # set Multi-index
        merchandise_database.index = pd.MultiIndex.from_tuples(merchandise_database.index)

        # reset the index to apply country converter
        merchandise_database = merchandise_database.reset_index()
        # apply country converter
        merchandise_database.level_0 = coco.convert(merchandise_database.level_0, to='EXIO3')
        # restore index
        merchandise_database = merchandise_database.set_index(['level_0', 'level_1'])
        merchandise_database.index.names = None, None

        # groupby on country/sector (e.g., there were multiple 'WL' after applying coco)
        merchandise_database = merchandise_database.groupby(merchandise_database.index).sum()

        # restore multi-index
        merchandise_database.index = pd.MultiIndex.from_tuples(merchandise_database.index)

        # reindexing to ensure all sectors are here, fill missing ones with zero values
        self.merchandise_imports = merchandise_database.reindex(pd.MultiIndex.from_product([
            merchandise_database.index.levels[0], [i[1] for i in self.commodities]])).fillna(0)

    def link_merchandise_database_to_openio(self):
        """
        Linking the international trade merchandise database of Statistics Canada to openIO-Canada.
        :return:
        """

        # the absolute values of self.merchandise_imports do not matter
        # we only use those to calculate a weighted average of imports per country
        for product in self.merchandise_imports.index.levels[1]:
            total = self.merchandise_imports.loc(axis=0)[:, product].sum()
            for region in self.merchandise_imports.index.levels[0]:
                self.merchandise_imports.loc(axis=0)[region, product] /= total

        # Nan values showing up from 0/0 operations
        self.merchandise_imports = self.merchandise_imports.fillna(0)

        # scale up international imports from international trade database to international imports according to openIO
        self.merchandise_imports_scaled = pd.DataFrame()

        df = self.who_uses_int_imports.groupby(axis=0, level=1).sum()
        df = pd.concat([df] * len(self.merchandise_imports.index.levels[0]))
        df.index = pd.MultiIndex.from_product(
            [self.merchandise_imports.index.levels[0], self.who_uses_int_imports.index.levels[1]])

        for product in self.merchandise_imports.index.levels[1]:
            dff = (df.loc(axis=0)[:, product].T * self.merchandise_imports.loc(axis=0)[:, product].iloc[:, 0]).T
            self.merchandise_imports_scaled = pd.concat([self.merchandise_imports_scaled, dff])

        self.merchandise_imports_scaled = self.merchandise_imports_scaled.sort_index()

    def gimme_symmetric_iot(self):
        """
        Transforms Supply and Use tables to symmetric IO tables and transforms Y from product to industries if
        selected classification is "industry"
        :return: self.A, self.R and self.Y
        """
        self.inv_q = pd.DataFrame(np.diag((1 / self.q.sum(axis=1)).replace(np.inf, 0)), self.q.index, self.q.index)
        self.inv_g = pd.DataFrame(np.diag((1 / self.g.sum()).replace(np.inf, 0)), self.g.columns, self.g.columns)

        if self.assumption == "industry technology" and self.classification == "product":
            self.A = self.U.dot(self.inv_g.dot(self.V.T)).dot(self.inv_q)
            self.R = self.W.dot(self.inv_g.dot(self.V.T)).dot(self.inv_q)
            if self.exiobase_folder:
                intermediary_demand = self.merchandise_imports_scaled.reindex(self.U.columns,axis=1).dot(
                    self.inv_g.dot(self.V.T)).dot(self.inv_q)
                intermediary_demand.columns = intermediary_demand.columns.tolist()
                self.merchandise_imports_scaled = pd.concat([intermediary_demand,
                                                             self.merchandise_imports_scaled.reindex(
                                                                 self.Y.columns,axis=1)], axis=1)

        elif self.assumption == "fixed industry sales structure" and self.classification == "industry":
            self.A = self.V.T.dot(self.inv_q).dot(self.U).dot(self.inv_g)
            self.R = self.W.dot(self.inv_g)
            # TODO check the Y in industries transformation
            self.Y = self.V.dot(self.inv_g).T.dot(self.Y)

    def link_international_trade_data_to_exiobase(self):
        """
        Linking the data from the international merchandise trade database, which was previously linked to openIO-Canada,
        to exiobase.
        :return:
        """

        # loading Exiobase
        io = pymrio.parse_exiobase3(self.exiobase_folder)

        # save the matrices from exiobase because we need them later
        self.A_exio = io.A.copy()
        self.S_exio = io.satellite.S.copy()
        self.F_exio = io.satellite.F.copy()
        # millions euros to euros
        self.S_exio.iloc[9:] /= 1000000
        self.unit_exio = io.satellite.unit.copy()
        self.unit_exio.columns = ['Unit']

        # loading concordances between exiobase classification and IOIC
        ioic_exio = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/IOIC_EXIOBASE.xlsx'),
                                  'commodities')
        ioic_exio = ioic_exio[2:].drop('IOIC Detail level - EXIOBASE', axis=1).set_index('Unnamed: 1').fillna(0)
        ioic_exio.index.name = None
        ioic_exio.index = [{j[0]: j[1] for j in self.commodities}[i] for i in ioic_exio.index]

        # determine the Canadian imports according to Exiobase
        canadian_imports_exio = io.A.loc[:, 'CA'].sum(1).drop('CA', axis=0, level=0)

        all_imports = self.who_uses_int_imports.groupby(axis=0, level=1).sum().sum(1)
        all_imports = all_imports[all_imports != 0].index.tolist()

        covered = self.merchandise_imports_scaled[self.merchandise_imports_scaled != 0].dropna(how='all').groupby(
            axis=0, level=1).sum().index.tolist()
        uncovered = [i for i in all_imports if i not in covered]

        # link to exiobase
        link_openio_exio = pd.DataFrame()
        not_traded = {}

        for merchandise in self.merchandise_imports_scaled.index.levels[1]:
            # check if there is trading happening for the uncovered commodity or not
            if self.who_uses_int_imports.groupby(axis=0, level=1).sum().loc[merchandise].sum() != 0:
                # 1 for 1 with exiobase -> easy
                if ioic_exio.loc[merchandise].sum() == 1:
                    exio_sector = ioic_exio.loc[merchandise][ioic_exio.loc[merchandise] == 1].index[0]
                    dff = self.merchandise_imports_scaled.loc(axis=0)[:, merchandise]
                    dff.index = [(i[0], exio_sector) for i in dff.index]
                    link_openio_exio = pd.concat([link_openio_exio, dff])
                # 1 for many with exiobase -> headscratcher
                elif ioic_exio.loc[merchandise].sum() > 1:
                    exio_sector = ioic_exio.loc[merchandise][ioic_exio.loc[merchandise] == 1].index.tolist()
                    dff = self.merchandise_imports_scaled.loc(axis=0)[:, merchandise].copy()
                    dff = pd.concat([dff] * len(exio_sector))
                    dff = dff.sort_index()
                    dff.index = pd.MultiIndex.from_product([dff.index.levels[0], exio_sector])
                    for region in dff.index.levels[0]:
                        dfff = (dff.loc[region].T *
                                (canadian_imports_exio.loc(axis=0)[region, exio_sector] /
                                 canadian_imports_exio.loc(axis=0)[region, exio_sector].sum()).loc[region]).T
                        # if our calculations shows imports (e.g., fertilizers from Bulgaria) for a product but there
                        # are not seen in exiobase, then we rely on io.x to distribute between commodities
                        if not np.isclose(
                                self.merchandise_imports_scaled.loc(axis=0)[:,
                                merchandise].loc[region].sum().sum(), dfff.sum().sum()):
                            dfff = (dff.loc[region].T *
                                    (io.x.loc(axis=0)[region, exio_sector].iloc[:, 0] /
                                     io.x.loc(axis=0)[region, exio_sector].iloc[:, 0].sum()).loc[region]).T
                        # if the product is simply not produced at all by the country according to exiobase, isolate the value in a dict
                        if not np.isclose(dff.loc[region].iloc[0].sum(), dfff.sum().sum()):
                            not_traded[(region, merchandise)] = [exio_sector, dff.loc[region].iloc[0].sum()]
                        dfff.index = pd.MultiIndex.from_product([[region], dfff.index])
                        link_openio_exio = pd.concat([link_openio_exio, dfff])
                        link_openio_exio.index = pd.MultiIndex.from_tuples(link_openio_exio.index)
                else:
                    print(merchandise + ' is not linked to any Exiobase sector!')

        link_openio_exio.index = pd.MultiIndex.from_tuples(link_openio_exio.index)
        link_openio_exio = link_openio_exio.groupby(link_openio_exio.index).sum()
        link_openio_exio.index = pd.MultiIndex.from_tuples(link_openio_exio.index)
        link_openio_exio = link_openio_exio.reindex(io.A.index).fillna(0)

        df = self.who_uses_int_imports.groupby(axis=0, level=1).sum()
        df = pd.concat([df] * len(self.merchandise_imports_scaled.index.levels[0]))
        df.index = pd.MultiIndex.from_product(
            [self.merchandise_imports_scaled.index.levels[0], self.who_uses_int_imports.index.levels[1]])

        service_imports = pd.DataFrame()

        for sector in uncovered:
            # check if there is trading happening for the uncovered commodity or not
            if self.who_uses_int_imports.groupby(axis=0, level=1).sum().loc[sector].sum() != 0:
                # 1 for 1 with exiobase -> easy
                if ioic_exio.loc[sector].sum() == 1:
                    exio_sector = ioic_exio.loc[sector][ioic_exio.loc[sector] == 1].index[0]
                    dff = canadian_imports_exio.loc(axis=0)[:, exio_sector]
                    dff = dff.sort_index()
                    dff.index = df.loc(axis=0)[:, sector].index
                    dff = (df.loc(axis=0)[:, sector].T * dff / dff.sum()).T
                    dff.index = pd.MultiIndex.from_product([dff.index.levels[0], [exio_sector]])
                    service_imports = pd.concat([service_imports, dff.fillna(0)])
                # 1 for many with exiobase -> headscratcher
                else:
                    exio_sector = ioic_exio.loc[sector][ioic_exio.loc[sector] == 1].index.tolist()
                    dff = pd.concat([df.loc(axis=0)[:, sector]] * len(exio_sector))
                    dff.index = pd.MultiIndex.from_product([df.index.levels[0], exio_sector])
                    dff = dff.sort_index()
                    dff = (dff.T * (canadian_imports_exio.loc(axis=0)[:, exio_sector] /
                                    canadian_imports_exio.loc(axis=0)[:, exio_sector].sum()).sort_index()).T
                    # if the product is simply not produced at all by the country according to exiobase, isolate the value in a dict
                    if not np.isclose(dff.loc[region].iloc[0].sum(), dff.sum().sum()):
                        not_traded[(region, merchandise)] = [exio_sector, dff.loc[region].iloc[0].sum()]
                    service_imports = pd.concat([service_imports, dff.fillna(0)])

        service_imports_intermediary = service_imports.reindex(self.U.columns, axis=1).dot(
            self.inv_g.dot(self.V.T)).dot(self.inv_q)
        service_imports_intermediary.columns = service_imports_intermediary.columns.tolist()
        service_imports = pd.concat([service_imports_intermediary, service_imports.reindex(self.Y.columns, axis=1)],
                                    axis=1)
        service_imports = service_imports.groupby(service_imports.index).sum()
        service_imports = service_imports.reindex(link_openio_exio.index).fillna(0)

        # distribute the link matrix between industries and final demands
        self.link_openio_exio_technosphere = (link_openio_exio + service_imports).reindex(self.A.columns, axis=1)
        self.link_openio_exio_final_demands = (link_openio_exio + service_imports).reindex(self.Y.columns, axis=1)

        # check financial balance is respected before converting to euros
        assert (self.A.sum() + self.R.sum() + self.link_openio_exio_technosphere.sum())[
                   (self.A.sum() + self.R.sum() + self.link_openio_exio_technosphere.sum()) < 0.95].sum() == 0

        # convert from CAD to EURO (https://www.bankofcanada.ca/rates/exchange/annual-average-exchange-rates/)
        if self.year == 2017:
            self.link_openio_exio_technosphere /= 1.465
            self.link_openio_exio_final_demands /= 1.465
        elif self.year == 2018:
            self.link_openio_exio_technosphere /= 1.5302
            self.link_openio_exio_final_demands /= 1.5302
        elif self.year == 2019:
            self.link_openio_exio_technosphere /= 1.4856
            self.link_openio_exio_final_demands /= 1.4856

    def concatenate_matrices(self):
        """
        Concatenate openIO-Canada matrices to Exiobase matrices and the link between them.
        :return:
        """

        # concat international trade with interprovincial trade
        self.A = pd.concat([self.A, self.link_openio_exio_technosphere])
        # concat openIO-Canada with exiobase to get the full technology matrix
        df = pd.concat([pd.DataFrame(0, index=self.A.columns, columns=self.A_exio.columns), self.A_exio])
        self.A = pd.concat([self.A, df], axis=1)

        # concat interprovincial and international trade for final demands
        self.Y = pd.concat([self.Y, self.link_openio_exio_final_demands])

    def extract_environmental_data(self):
        """
        Extracts the data from the NPRI file
        :return: self.F but linked to NAICS codes
        """
        # Tab name changes with selected year, so identify it using "INRP-NPRI"
        emissions = self.NPRI[[i for i in self.NPRI.keys() if "INRP-NPRI" in i][0]]
        emissions.columns = list(zip(emissions.iloc[0].ffill().tolist(), emissions.iloc[2]))
        emissions = emissions.iloc[3:]
        # selecting the relevant columns from the file
        emissions = emissions.loc[:, [i for i in emissions.columns if
                                      (i[1] in
                                       ['NAICS 6 Code', 'CAS Number', 'Substance Name (English)', 'Units', 'Province']
                                       or 'Total' in i[1] and 'Air' in i[0]
                                       or 'Total' in i[1] and 'Water' in i[0]
                                       or 'Total' in i[1] and 'Land' in i[0])]].fillna(0)
        # renaming the columns
        emissions.columns = ['Province', 'NAICS 6 Code', 'CAS Number', 'Substance Name', 'Units', 'Emissions to air',
                             'Emissions to water', 'Emissions to land']

        # somehow the NPRI manages to have entries without NAICS codes... Remove them
        no_naics_code_entries = emissions.loc[:, 'NAICS 6 Code'][emissions.loc[:, 'NAICS 6 Code'] == 0].index
        emissions.drop(no_naics_code_entries, inplace=True)

        # NAICS codes as strings and not integers
        emissions.loc[:, 'NAICS 6 Code'] = emissions.loc[:, 'NAICS 6 Code'].astype('str')

        # extracting metadata for substances
        temp_df = emissions.copy()
        temp_df.set_index('Substance Name', inplace=True)
        temp_df = temp_df.groupby(temp_df.index).head(n=1)
        # separating the metadata for emissions (CAS and units)
        self.emission_metadata = pd.DataFrame('', index=temp_df.index, columns=['CAS Number', 'Unit'])
        for emission in temp_df.index:
            self.emission_metadata.loc[emission, 'CAS Number'] = temp_df.loc[emission, 'CAS Number']
            self.emission_metadata.loc[emission, 'Unit'] = temp_df.loc[emission, 'Units']
        del temp_df

        self.F = pd.pivot_table(data=emissions, index=['Province', 'Substance Name'],
                                columns=['Province', 'NAICS 6 Code'], aggfunc=np.sum).fillna(0)
        # renaming compartments
        self.F.columns.set_levels(['Air', 'Water', 'Soil'], level=0, inplace=True)
        # renaming the names of the columns indexes
        self.F.columns = self.F.columns.rename(['compartment', 'Province', 'NAICS'])
        # reorder multi index to have province as first level
        self.F = self.F.reorder_levels(['Province', 'compartment', 'NAICS'], axis=1)
        # match compartments with emissions and not to provinces
        self.F = self.F.T.unstack('compartment').T[self.F.T.unstack('compartment').T != 0].fillna(0)
        # identify emissions that are in tonnes
        emissions_to_rescale = [i for i in self.emission_metadata.index if
                                self.emission_metadata.loc[i, 'Unit'] == 'tonnes']
        # convert them to kg
        self.F.loc(axis=0)[:, emissions_to_rescale] *= 1000
        self.emission_metadata.loc[emissions_to_rescale, 'Unit'] = 'kg'
        # same thing for emissions in grams
        emissions_to_rescale = [i for i in self.emission_metadata.index if
                                self.emission_metadata.loc[i, 'Unit'] == 'grams']
        self.F.loc(axis=0)[:, emissions_to_rescale] /= 1000
        self.emission_metadata.loc[emissions_to_rescale, 'Unit'] = 'kg'

        # harmonizing emissions across provinces, set to zero if missing initially
        new_index = pd.MultiIndex.from_product(
            [self.matching_dict, self.emission_metadata.sort_index().index, ['Air', 'Water', 'Soil']])
        self.F = self.F.reindex(new_index).fillna(0)

        # harmonizing NAICS codes across provinces this time
        self.F = self.F.T.reindex(
            pd.MultiIndex.from_product([self.F.columns.levels[0], self.F.columns.levels[1]])).T.fillna(0)

    def match_npri_data_to_iots(self):

        total_emissions_origin = self.F.sum().sum()

        # load and format concordances file
        concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/NPRI_concordance.xlsx'),
                                    self.level_of_detail)
        concordance.set_index('NAICS 6 Code', inplace=True)
        concordance.drop('NAICS 6 Sector Name (English)', axis=1, inplace=True)

        # splitting emissions between private and public sectors
        if self.level_of_detail == 'Summary level':
            self.split_private_public_sectors(NAICS_code=['611210', '611310', '611510'], IOIC_code='GS610')
        elif self.level_of_detail == 'Link-1961 level':
            self.split_private_public_sectors(NAICS_code=['611210', '611510'], IOIC_code='GS611B0')
            self.split_private_public_sectors(NAICS_code='611310', IOIC_code='GS61130')
        elif self.level_of_detail in ['Link-1997 level', 'Detail level']:
            self.split_private_public_sectors(NAICS_code='611210', IOIC_code='GS611200')
            self.split_private_public_sectors(NAICS_code='611310', IOIC_code='GS611300')
            self.split_private_public_sectors(NAICS_code='611510', IOIC_code='GS611A00')

        # switch NAICS codes in self.F for corresponding IOIC codes (from concordances file)
        IOIC_index = []
        for NAICS in self.F.columns:
            try:
                IOIC_index.append((NAICS[0], concordance.loc[int(NAICS[1]), 'IOIC']))
            except ValueError:
                IOIC_index.append(NAICS)
        self.F.columns = pd.MultiIndex.from_tuples(IOIC_index)

        # adding emissions from same sectors together (summary level is more aggregated than NAICS 6 Code)
        self.F = self.F.groupby(self.F.columns, axis=1).sum()
        # reordering columns
        self.F = self.F.T.reindex(
            pd.MultiIndex.from_product([self.matching_dict, [i[0] for i in self.industries]])).T.fillna(0)
        # changing codes for actual names of the sectors
        self.F.columns = pd.MultiIndex.from_product([self.matching_dict, [i[1] for i in self.industries]])

        # assert that nearly all emissions present in the NPRI were successfully transferred in self.F
        assert self.F.sum().sum() / total_emissions_origin > 0.98
        assert self.F.sum().sum() / total_emissions_origin < 1.02

    def match_ghg_accounts_to_iots(self):
        """
        Method matching GHG accounts to IOIC classification selected by the user
        :return: self.F and self.FY with GHG flows included
        """

        if self.year in [2017, 2018]:
            GHG = pd.read_excel(
                pkg_resources.resource_stream(__name__, '/Data/Environmental_data/GHG_emissions_by_gas_RY2017-RY2018.xlsx'),
                'L61 ghg emissions by gas')
            GHG = GHG.loc[
                [i for i in GHG.index if GHG.loc[i, 'Reference Year'] == self.year and GHG.Geography[i] != 'Canada']]
        elif self.year == 2019:
            GHG = pd.read_excel(
                pkg_resources.resource_stream(__name__, '/Data/Environmental_data/GHG_emissions_by_gas_RY2019.xlsx'),
                'L61 ghg emissions by gas')
            GHG = GHG.loc[[i for i in GHG.index if GHG.Geography[i] != 'Canada']]
        else:
            GHG = pd.read_excel(
                pkg_resources.resource_stream(__name__, '/Data/Environmental_data/GHG_emissions_by_gas_RY2017-RY2018.xlsx'),
                'L61 ghg emissions by gas')
            GHG = GHG.loc[
                [i for i in GHG.index if GHG.loc[i, 'Reference Year'] == 2017 and GHG.Geography[i] != 'Canada']]

        # adding HFCs and PFCs emissions
        hfcs = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Environmental_data/SF6_HFC_PFC_emissions.xlsx'),
                             'GHG Emissions GES 2004-2021')

        hfcs = hfcs.loc[:, ['Reference Year / Année de référence',
                            "Facility Province or Territory / Province ou territoire de l'installation",
                            "Facility NAICS Code / Code SCIAN de l'installation",
                            'SF6 (tonnes)', 'HFC-32 (tonnes)', 'HFC-41 (tonnes)', 'HFC-43-10mee (tonnes)',
                            'HFC-125 (tonnes)', 'HFC-134 (tonnes)', 'HFC-134a (tonnes)', 'HFC-143 (tonnes)',
                            'HFC-143a (tonnes)',
                            'HFC-152a (tonnes)', 'HFC-227ea (tonnes)', 'HFC-236fa (tonnes)', 'HFC-245ca (tonnes)',
                            'CF4 (tonnes)',
                            'C2F6 (tonnes)', 'C3F8 (tonnes)', 'C4F10 (tonnes)', 'C4F8 (tonnes)', 'C5F12 (tonnes)',
                            'C6F14 (tonnes)']]

        conc = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/NAICS-IOIC.xlsx'))
        hfcs = hfcs.merge(conc, left_on="Facility NAICS Code / Code SCIAN de l'installation", right_on='NAICS 6 Code')
        hfcs = hfcs.drop(["Facility NAICS Code / Code SCIAN de l'installation",
                          'NAICS 6 Sector Name (English)', 'NAICS 6 Code'], axis=1)
        hfcs = hfcs.set_index(['Reference Year / Année de référence',
                               "Facility Province or Territory / Province ou territoire de l'installation",
                               'IOIC'])

        hfcs = hfcs.loc[self.year]
        hfcs.index = pd.MultiIndex.from_tuples([({v: k for k, v in self.matching_dict.items()}[i[0]],
                                                 dict(self.industries)[i[1]]) for i in hfcs.index])

        hfcs = hfcs.groupby(hfcs.index).sum()
        hfcs.index = pd.MultiIndex.from_tuples(hfcs.index)
        hfcs = hfcs.fillna(0)
        # renaming
        hfcs.columns = ['Sulfur hexafluoride', 'HFC-32', 'HFC-41', 'HFC-4310mee', 'HFC-125', 'HFC-134', 'HFC-134a',
                        'HFC-143', 'HFC-143a', 'HFC-152a', 'HFC-227ea', 'HFC-236fa', 'HFC-245ca', 'CFC-14', 'CFC-16',
                        'Perfluoropropane', 'Perfluorobutane', 'Perfluorobutene', 'Perfluoropentane', 'Perfluorohexane']

        # kilotonnes to kgs
        GHG.loc[:, ['CO2', 'CH4', 'N2O']] *= 1000000

        if not self.final_demand_aggregated and self.level_of_detail not in ['Summary level', 'Link-1961 level']:
            # start with the households emissions
            Household_GHG = GHG.loc[[i for i in GHG.index if 'PEH' in GHG.loc[i, 'IOIC']]]
            Household_GHG.drop(['Reference Year', 'Description', 'F_Description'], axis=1, inplace=True)
            # assume all direct emissions from home appliances come from "Other fuels"
            Household_GHG.IOIC = ['Other fuels' if i == 'PEH1' else 'Fuels and lubricants' for i in Household_GHG.IOIC]
            Household_GHG.Geography = [{v: k for k, v in self.matching_dict.items()}[i] for i in
                                       Household_GHG.Geography]
            Household_GHG = pd.pivot_table(data=Household_GHG, values=['CO2', 'CH4', 'N2O'],
                                           columns=['Geography', 'IOIC'])
            Household_GHG.columns = [(i[0], "Household final consumption expenditure", i[1]) for i in
                                     Household_GHG.columns]
            Household_GHG = Household_GHG.reindex(self.Y.columns, axis=1).fillna(0)
            # spatialization
            Household_GHG = pd.concat([Household_GHG] * len(self.matching_dict))
            Household_GHG.index = pd.MultiIndex.from_product([self.matching_dict,
                                                              ['Methane', 'Carbon dioxide', 'Dinitrogen monoxide'],
                                                              ['Air']]).drop_duplicates()
            for province in Household_GHG.columns.levels[0]:
                Household_GHG.loc[[i for i in Household_GHG.columns.levels[0] if i != province], province] = 0

            # create FY and update it with GHG emissions from households
            self.FY = pd.DataFrame(0, index=pd.MultiIndex.from_product(
                [self.matching_dict, ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide'], ['Air']]).drop_duplicates(),
                                   columns=self.Y.columns)
            self.FY.update(Household_GHG)
        else:
            # start with the households emissions
            Household_GHG = GHG.loc[[i for i in GHG.index if 'PEH' in GHG.loc[i, 'IOIC']]]
            Household_GHG = Household_GHG.groupby('Geography').sum().drop('Reference Year', axis=1).T
            Household_GHG.index = ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide']
            Household_GHG.columns = [{v: k for k, v in self.matching_dict.items()}[i] for i in Household_GHG.columns]
            Household_GHG.columns = pd.MultiIndex.from_product(
                [Household_GHG.columns, ['Household final consumption expenditure']])
            self.FY = pd.DataFrame(0, Household_GHG.index, self.Y.columns).merge(Household_GHG, 'right').fillna(0)
            self.FY.index = pd.MultiIndex.from_product([Household_GHG.index.tolist(), ['Air']])
            # spatialization
            self.FY = pd.concat([self.FY] * len(self.FY.columns.levels[0]), axis=0)
            self.FY.index = pd.MultiIndex.from_product(
                [list(self.matching_dict.keys()), ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide'], ['Air']])
            for province in self.FY.columns.levels[0]:
                self.FY.loc[[i for i in self.FY.index.levels[0] if i != province], province] = 0

        # Now the emissions from production
        GHG.set_index(pd.MultiIndex.from_tuples(tuple(
            list(zip([{v: k for k, v in self.matching_dict.items()}[i] for i in GHG.Geography], GHG.IOIC.tolist())))),
                      inplace=True)
        GHG.drop(['IOIC', 'Reference Year', 'Geography', 'Description', 'F_Description'], axis=1, inplace=True)
        GHG.drop([i for i in GHG.index if re.search(r'^FC', i[1])
                  or re.search(r'^PEH', i[1])
                  or re.search(r'^Total', i[1])], inplace=True)
        GHG.columns = ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide']

        concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/GHG_concordance.xlsx'),
                                    self.level_of_detail)
        concordance.set_index('GHG codes', inplace=True)

        if self.level_of_detail in ['Summary level', 'Link-1961 level']:
            # transform GHG accounts sectors to IOIC sectors
            GHG.index = pd.MultiIndex.from_tuples([(i[0], concordance.loc[i[1], 'IOIC']) for i in GHG.index])
            # some sectors are not linked to IOIC (specifically weird Canabis sectors), drop them
            if len([i for i in GHG.index if type(i[1]) == float]) != 0:
                GHG.drop([i for i in GHG.index if type(i[1]) == float], inplace=True)
            # grouping emissions from same sectors
            GHG = GHG.groupby(GHG.index).sum()
            GHG.index = pd.MultiIndex.from_tuples(GHG.index)
            # reindex to make sure dataframe is ordered as in dictionary
            GHG = GHG.reindex(pd.MultiIndex.from_product([self.matching_dict, [i[0] for i in self.industries]]))
            # switching codes for readable names
            GHG.index = pd.MultiIndex.from_product([self.matching_dict, [i[1] for i in self.industries]])

            # spatializing GHG emissions in case we later regionalize impacts (even though it's useless for climate change)
            GHG = pd.concat([GHG] * len(GHG.index.levels[0]), axis=1)
            GHG.columns = pd.MultiIndex.from_product(
                [self.matching_dict, ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide'], ['Air']])
            # emissions takes place in the province of the trade
            for province in GHG.index.levels[0]:
                GHG.loc[province, [i for i in GHG.index.levels[0] if i != province]] = 0
            # add GHG emissions to other pollutants
            self.F = pd.concat([self.F, GHG.T])
            self.F.index = pd.MultiIndex.from_tuples(self.F.index)

        elif self.level_of_detail in ['Link-1997 level', 'Detail level']:
            # dropping empty sectors (mostly Cannabis related)
            to_drop = concordance.loc[concordance.loc[:, 'IOIC'].isna()].index
            concordance.drop(to_drop, inplace=True)
            ghgs = pd.DataFrame()
            for code in concordance.index:
                # L97 and D levels are more precise than GHG accounts, we use market share to distribute GHGs
                sectors_to_split = [i[1] for i in self.industries if
                                    i[0] in concordance.loc[code].dropna().values.tolist()]
                output_sectors_to_split = self.V.loc[:,
                                          [i for i in self.V.columns if i[1] in sectors_to_split]].sum()
                share_sectors_to_split = pd.DataFrame(0, output_sectors_to_split.index,
                                                      ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide'])
                for province in output_sectors_to_split.index.levels[0]:
                    df = ((output_sectors_to_split.loc[province] / output_sectors_to_split.loc[province].sum()).fillna(0).values)
                    # hardcoded 3 because 3 GHGs: CO2, CH4, N2O
                    share_sectors_to_split.loc[province] = (pd.DataFrame([df] * 3,
                                                                         index=['Carbon dioxide', 'Methane', 'Dinitrogen monoxide'],
                                                                         columns=sectors_to_split).T
                                                            * GHG.loc(axis=0)[:, code].loc[province].values).values
                ghgs = pd.concat([ghgs, share_sectors_to_split])

            # merging with HFCs and PFCs data
            ghgs = ghgs.join(hfcs * 1000).fillna(0)
            ghgs.columns = [i.split(' (')[0] if i.split(' (')[0] != 'SF6' else 'Sulfur hexafluoride' for i in
                            ghgs.columns]

            # spatializing GHG emissions
            list_ghgs = ghgs.columns.tolist()
            ghgs = pd.concat([ghgs] * len(ghgs.index.levels[0]), axis=1)
            ghgs.columns = pd.MultiIndex.from_product(
                [list(self.matching_dict.keys()), list_ghgs, ['Air']])
            for province in ghgs.columns.levels[0]:
                ghgs.loc[[i for i in ghgs.index.levels[0] if i != province], province] = 0

            # adding GHG accounts to pollutants
            self.F = pd.concat([self.F, ghgs.T])

            # reindexing
            self.F = self.F.reindex(self.U.columns, axis=1)

        self.emission_metadata.loc['Carbon dioxide', 'CAS Number'] = '124-38-9'
        self.emission_metadata.loc['Methane', 'CAS Number'] = '74-82-8'
        self.emission_metadata.loc['Dinitrogen monoxide', 'CAS Number'] = '10024-97-2'
        self.emission_metadata.loc['Sulfur hexafluoride', 'CAS Number'] = '2551-62-4'
        self.emission_metadata.loc['HFC-32', 'CAS Number'] = '75-10-5'
        self.emission_metadata.loc['HFC-41', 'CAS Number'] = '593-53-3'
        self.emission_metadata.loc['HFC-4310mee', 'CAS Number'] = '138495-42-8'
        self.emission_metadata.loc['HFC-125', 'CAS Number'] = '354-33-6'
        self.emission_metadata.loc['HFC-134', 'CAS Number'] = '811-97-2'
        self.emission_metadata.loc['HFC-134a', 'CAS Number'] = '811-97-2'
        self.emission_metadata.loc['HFC-143', 'CAS Number'] = '420-46-2'
        self.emission_metadata.loc['HFC-143a', 'CAS Number'] = '420-46-2'
        self.emission_metadata.loc['HFC-152a', 'CAS Number'] = '75-37-6'
        self.emission_metadata.loc['HFC-227ea', 'CAS Number'] = '431-89-0'
        self.emission_metadata.loc['HFC-236fa', 'CAS Number'] = '690-39-1'
        self.emission_metadata.loc['HFC-245ca', 'CAS Number'] = '679-86-7'
        self.emission_metadata.loc['CFC-14', 'CAS Number'] = '75-73-0'
        self.emission_metadata.loc['CFC-16', 'CAS Number'] = '76-16-4'
        self.emission_metadata.loc['Perfluoropropane', 'CAS Number'] = '76-19-7'
        self.emission_metadata.loc['Perfluorobutane', 'CAS Number'] = '355-25-9'
        self.emission_metadata.loc['Perfluorobutene', 'CAS Number'] = '382-21-8'
        self.emission_metadata.loc['Perfluoropentane', 'CAS Number'] = '678-26-2'
        self.emission_metadata.loc['Perfluorohexane', 'CAS Number'] = '355-42-0'

        self.emission_metadata.loc[list_ghgs, 'Unit'] = 'kg'

    def match_water_accounts_to_iots(self):
        """
        Method matching water accounts to IOIC classification selected by the user
        :return: self.F and self.FY with GHG flows included
        """
        # load the water use data from STATCAN
        water = pd.read_csv(pkg_resources.resource_stream(__name__, '/Data/Environmental_data/water_use.csv'))

        # Only odd years from 2009 to 2017
        if self.year == 2010:
            year_for_water = 2011
        elif self.year == 2012:
            year_for_water = 2013
        elif self.year == 2014:
            year_for_water = 2015
        elif self.year == 2016:
            year_for_water = 2015
        elif self.year > 2017:
            year_for_water = 2017
        else:
            year_for_water = self.year
        # select the year of the data
        water = water.loc[
            [i for i in water.index if water.REF_DATE[i] == int(year_for_water)], ['Sector', 'VALUE']].fillna(0)

        # convert into cubic meters
        water.VALUE *= 1000

        if not self.final_demand_aggregated and self.level_of_detail not in ['Summary level','Link-1961 level']:
            fd_water = water.loc[[i for i in water.index if water.Sector[i] == 'Households']]
            water_provincial_use_distribution = self.Y.loc(axis=0)[:,
                                                'Water delivered by water works and irrigation systems'].loc(axis=1)[:,
                                                'Household final consumption expenditure'].sum(axis=1)
            water_provincial_use_distribution /= water_provincial_use_distribution.sum()
            water_provincial_use_distribution *= fd_water.VALUE.iloc[0]
            water_provincial_use_distribution = pd.DataFrame(water_provincial_use_distribution, columns=['Water']).T
            water_provincial_use_distribution = pd.concat([water_provincial_use_distribution] * len(self.matching_dict))
            water_provincial_use_distribution.index = pd.MultiIndex.from_product([self.matching_dict,
                                                                                  water_provincial_use_distribution.index,
                                                                                  ['Water']]).drop_duplicates()
            for province in water_provincial_use_distribution.index.levels[0]:
                water_provincial_use_distribution.loc[
                    province, [i for i in water_provincial_use_distribution.columns if i[0] != province]] = 0
            water_provincial_use_distribution.columns = pd.MultiIndex.from_product([self.matching_dict,
                                                                                    ["Household final consumption expenditure"],
                                                                                    ["Water supply and sanitation services"]])
            self.FY = pd.concat([self.FY, water_provincial_use_distribution.reindex(self.Y.columns, axis=1).fillna(0)])
        else:
            # water use from households
            FD_water = water.loc[[i for i in water.index if water.Sector[i] == 'Households']]
            # national water use will be distributed depending on the amount of $ spent by households in a given province
            provincial_FD_consumption_distribution = self.Y.loc(axis=1)[:,
                                                     'Household final consumption expenditure'].sum() / self.Y.loc(
                axis=1)[:, 'Household final consumption expenditure'].sum().sum()
            FD_water = provincial_FD_consumption_distribution * FD_water.VALUE.values
            # spatializing
            FD_water = pd.concat([FD_water] * len(FD_water.index.levels[0]), axis=1)
            FD_water.columns = pd.MultiIndex.from_product([self.matching_dict.keys(), ['Water'], ['Water']])
            FD_water = FD_water.T
            for province in FD_water.index.levels[0]:
                FD_water.loc[province, [i for i in FD_water.columns if i[0] != province]] = 0
            FD_water = FD_water.T.reindex(self.Y.columns).T.fillna(0)
            self.FY = pd.concat([self.FY, FD_water])

        # format the names of the sector to match those used up till then
        water = water.loc[[i for i in water.index if '[' in water.Sector[i]]]
        water.Sector = [i.split('[')[1].split(']')[0] for i in water.Sector]
        water.drop([i for i in water.index if re.search(r'^FC', water.Sector.loc[i])], inplace=True)
        water.set_index('Sector', inplace=True)

        # load concordances matching water use data classification to the different classifications used in OpenIO
        concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/water_concordance.xlsx'),
                                    self.level_of_detail)
        concordance.set_index('Sector', inplace=True)
        # dropping potential empty sectors (mostly Cannabis related)
        to_drop = concordance.loc[concordance.loc[:, 'IOIC'].isna()].index
        concordance.drop(to_drop, inplace=True)

        water_flows = pd.DataFrame()
        if self.level_of_detail in ['Link-1961 level', 'Link-1997 level', 'Detail level']:
            for code in concordance.index:
                # Detail level is more precise than water accounts, we use market share to distribute water flows
                sectors_to_split = [i[1] for i in self.industries if
                                    i[0] in concordance.loc[code].dropna().values.tolist()]
                output_sectors_to_split = self.V.loc[:,
                                          [i for i in self.V.columns if i[1] in sectors_to_split]].sum()

                share_sectors_to_split = output_sectors_to_split / output_sectors_to_split.sum() * water.loc[
                    code, 'VALUE']
                water_flows = pd.concat([water_flows, share_sectors_to_split])
        elif self.level_of_detail == 'Summary level':
            water = pd.concat([water, concordance], axis=1)
            water.set_index('IOIC', inplace=True)
            water = water.groupby(water.index).sum()
            water.index = [dict(self.industries)[i] for i in water.index]
            water = water.reindex([i[1] for i in self.industries]).fillna(0)
            water_flows = pd.DataFrame()
            for sector in water.index:
                water_split = self.g.sum().loc(axis=0)[:, sector] / self.g.sum().loc(axis=0)[:, sector].sum() * \
                              water.loc[sector, 'VALUE']
                water_flows = pd.concat([water_flows, water_split])

        water_flows = water_flows.groupby(water_flows.index).sum().fillna(0)
        # multi index for the win
        water_flows.index = pd.MultiIndex.from_tuples(water_flows.index)
        water_flows.columns = ['Water']

        # spatializing water flows
        water_flows = pd.concat([water_flows.T] * len(water_flows.index.levels[0]))
        water_flows.index = pd.MultiIndex.from_product([self.matching_dict.keys(), ['Water'], ['Water']])
        water_flows = water_flows.T.reindex(self.F.columns).T
        for province in water_flows.index.levels[0]:
            water_flows.loc[province, [i for i in water_flows.columns if i[0] != province]] = 0

        # fillna(0) for cannabis industries
        self.F = pd.concat([self.F, water_flows]).fillna(0)

        self.emission_metadata.loc['Water', 'Unit'] = 'm3'

    def match_energy_accounts_to_iots(self):
        """
        Method matching energy accounts to IOIC classification selected by the user
        :return: self.F and self.FY with GHG flows included
        """
        NRG = pd.read_csv(pkg_resources.resource_stream(__name__, '/Data/Environmental_data/Energy_use.csv'))
        # select year of study
        NRG = NRG.loc[[i for i in NRG.index if NRG.REF_DATE[i] == self.year]]
        # keep households energy consumption in a specific dataframe
        NRG_FD = NRG.loc[[i for i in NRG.index if 'Households' in NRG.Sector[i]]]
        # keep industry energy consumption
        NRG = NRG.loc[[i for i in NRG.index if '[' in NRG.Sector[i]]]
        # extract sector codes
        NRG.Sector = [i.split('[')[1].split(']')[0] for i in NRG.Sector]
        # pivot into a dataframe
        NRG = NRG.pivot_table(values='VALUE', index=['Sector'], dropna=False).fillna(0)
        # remove fictive sectors
        NRG.drop([i for i in NRG.index if re.search(r'^FC', i)], inplace=True)

        # ------------ Industries ----------------

        # load concordance file
        concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/Energy_concordance.xlsx'),
                                    self.level_of_detail)
        concordance.set_index('NRG codes', inplace=True)
        # dropping empty sectors (mostly Cannabis related)
        to_drop = concordance.loc[concordance.loc[:, 'IOIC'].isna()].index
        concordance.drop(to_drop, inplace=True)

        # distributing energy use to more precise classifications based on market shares
        nrg = pd.DataFrame()
        for code in concordance.index:
            sectors_to_split = [i[1] for i in self.industries if
                                i[0] in concordance.loc[code].dropna().values.tolist()]
            output_sectors_to_split = self.V.loc[:,
                                      [i for i in self.V.columns if i[1] in sectors_to_split]].sum()
            output_sectors_to_split = output_sectors_to_split.groupby(axis=0, level=1).sum()
            share_sectors_to_split = output_sectors_to_split / output_sectors_to_split.sum()
            share_sectors_to_split *= NRG.loc[code, 'VALUE']
            nrg = pd.concat([nrg, share_sectors_to_split])

        # distributing national energy use to provinces based on market shares
        nrg_provincial = pd.DataFrame(0, index=pd.MultiIndex.from_product([self.matching_dict, nrg.index]),
                                      columns=['Energy'])

        for sector in nrg.index:
            share_province = self.g.loc(axis=1)[:, sector].sum(0) / self.g.loc(axis=1)[:, sector].sum(1).sum() * \
                             nrg.loc[sector].iloc[0]
            nrg_provincial.loc[share_province.index] = pd.DataFrame(share_province, columns=['Energy'])

        # adding to self.F
        self.F = pd.concat([self.F, nrg_provincial.reindex(self.F.columns).T])
        # cannabis stores are NaN values, we change that to zero values
        self.F = self.F.fillna(0)

        # ------------- Final demand -------------

        # pivot into a dataframe
        NRG_FD = NRG_FD.pivot_table(values='VALUE', index=['Sector'], dropna=False).fillna(0)
        # rename index to IOIC FD classification
        NRG_FD.index = ['Other fuels', 'Fuels and lubricants']

        if self.final_demand_aggregated:
            # distributing national final demand energy use to provinces based on market shares
            nrg_fd_provincial = pd.DataFrame(0, index=pd.MultiIndex.from_product(
                [self.matching_dict, ['Household final consumption expenditure']]), columns=['Energy'])
            share_province = self.Y.loc(axis=1)[:, 'Household final consumption expenditure'].sum() / self.Y.loc(
                axis=1)[:,'Household final consumption expenditure'].sum().sum() * NRG_FD.sum().sum()
            nrg_fd_provincial.loc[share_province.index] = pd.DataFrame(share_province, columns=['Energy'])
        else:
            # distributing national final demand energy use to provinces based on market shares
            nrg_fd_provincial = pd.DataFrame(0, index=pd.MultiIndex.from_product(
                [self.matching_dict, ['Household final consumption expenditure'], NRG_FD.index]), columns=['Energy'])

            for fd_sector in NRG_FD.index:
                share_province = self.Y.loc(axis=1)[:, :, fd_sector].sum() / self.Y.loc(axis=1)[:, :,
                                                                             fd_sector].sum().sum() * NRG_FD.loc[
                                     fd_sector, 'VALUE']
                nrg_fd_provincial.loc[share_province.index] = pd.DataFrame(share_province, columns=['Energy'])

        # adding to self.FY
        self.FY = pd.concat([self.FY, nrg_fd_provincial.reindex(self.Y.columns).fillna(0).T])
        # cannabis stores are NaN values, we change that to zero values
        self.FY = self.FY.fillna(0)

        self.emission_metadata.loc['Energy', 'Unit'] = 'TJ'

    def match_mineral_extraction_to_iots(self):
        """
        Method matching mineral extraction data from USGS to IOIC classification selected by the user
        :return: self.F with mineral flows included
        """
        xl = pd.read_excel(pkg_resources.resource_stream(
            __name__, '/Data/Environmental_data/Minerals_extracted_in_Canada.xlsx')).set_index('Unnamed: 0')
        xl.index.name = None

        with open(pkg_resources.resource_filename(__name__, '/Data/Concordances/concordance_metals.json'), 'r') as f:
            dict_data = json.load(f)

        distrib_minerals = pd.DataFrame()
        for mineral_sector in list(set(dict_data.values())):
            df = self.q.sum(1).loc(axis=0)[:, mineral_sector].copy()
            df /= df.sum()
            distrib_minerals = pd.concat([distrib_minerals, df])

        distrib_minerals.index = pd.MultiIndex.from_tuples(distrib_minerals.index)

        self.minerals = pd.DataFrame(0, index=dict_data, columns=self.q.index)

        if self.year in [2014, 2015, 2016, 2017, 2018]:
            for mineral in dict_data:
                df = xl.loc[mineral, self.year] * distrib_minerals.loc(axis=0)[:, dict_data[mineral]]
                df.columns = [mineral]
                df = df.T
                self.minerals.loc[mineral, df.columns] = df.loc[mineral]
        # no data for 2019 so use 2018 data
        elif self.year == 2019:
            for mineral in dict_data:
                df = xl.loc[mineral, 2018] * distrib_minerals.loc(axis=0)[:, dict_data[mineral]]
                df.columns = [mineral]
                df = df.T
                self.minerals.loc[mineral, df.columns] = df.loc[mineral]

        # convert from thousand carats to metric tons
        self.minerals.loc['Diamond'] *= 0.0002
        # Li content per spodumene: https://www2.bgs.ac.uk/mineralsuk/download/mineralProfiles/lithium_profile.pdf
        self.minerals.loc['Lithium'] *= 0.037
        # from metric tons to kgs
        self.minerals *= 1000

        self.emission_metadata = pd.concat([self.emission_metadata, pd.DataFrame('kg', index=self.minerals.index,
                                                                                 columns=['Unit'])])

    def characterization_matrix(self):
        """
        Produces a characterization matrix from IMPACT World+ file
        :return: self.C, self.methods_metadata
        """

        IW = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Characterization_factors/impact_world_plus_2.0_dev.xlsx'))

        pivoting = pd.pivot_table(IW, values='CF value', index=('Impact category', 'CF unit'),
                                  columns=['Elem flow name', 'Compartment', 'Sub-compartment']).fillna(0)

        try:
            concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/openIO_IW_concordance.xlsx'),
                                        str(self.NPRI_file_year))
        except ValueError:
            concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/openIO_IW_concordance.xlsx'),
                                        '2016')
        concordance.set_index('OpenIO flows', inplace=True)

        # applying concordance
        self.C = pd.DataFrame(0, pivoting.index, self.F.index.tolist() + self.minerals.index.tolist())
        for flow in self.C.columns:
            if type(flow) == tuple:
                try:
                    if concordance.loc[flow[1], 'IMPACT World+ flows'] is not None:
                        self.C.loc[:, [flow]] = pivoting.loc[:,
                                                [(concordance.loc[flow[1], 'IMPACT World+ flows'], flow[2],
                                                  '(unspecified)')]].values
                except KeyError:
                    pass
            # if type == str -> we are looking at the minerals extension -> hardcode Raw/in ground comp/subcomp
            elif type(flow) == str:
                try:
                    if concordance.loc[flow, 'IMPACT World+ flows'] is not None:
                        self.C.loc[:, [flow]] = pivoting.loc[:, [(concordance.loc[flow, 'IMPACT World+ flows'],
                                                                  'Raw', 'in ground')]].values
                except KeyError:
                    pass

        self.C.loc[('Water use', 'm3'), [i for i in self.C.columns if i[1] == 'Water']] = 1
        self.C.loc[('Energy use', 'TJ'), [i for i in self.C.columns if i == 'Energy']] = 1
        self.C = self.C.fillna(0)

        # some methods of IMPACT World+ do not make sense in our context, remove them
        self.C.drop(['Fossil and nuclear energy use',
                     'Ionizing radiations',
                     'Ionizing radiation, ecosystem quality',
                     'Ionizing radiation, human health',
                     'Land occupation, biodiversity',
                     'Land transformation, biodiversity',
                     'Thermally polluted water',
                     'Water availability, freshwater ecosystem',
                     'Water availability, human health',
                     'Water availability, terrestrial ecosystem',
                     'Water scarcity'], axis=0, level=0, inplace=True)

        # importing characterization matrix IMPACT World+/exiobase
        self.C_exio = pd.read_excel(pkg_resources.resource_stream(
            __name__, '/Data/Characterization_factors/impact_world_plus_2.0_exiobase.xlsx'), index_col='Unnamed: 0')
        self.C_exio.index = pd.MultiIndex.from_tuples(list(zip(
            [i.split(' (')[0] for i in self.C_exio.index],
            [i.split(' (')[1].split(')')[0] for i in self.C_exio.index])))

        self.C_exio.drop(['Fossil and nuclear energy use',
                         'Ionizing radiations',
                         'Ionizing radiation, ecosystem quality',
                         'Ionizing radiation, human health',
                         'Land occupation, biodiversity',
                         'Land transformation, biodiversity',
                         'Thermally polluted water',
                         'Water availability, freshwater ecosystem',
                         'Water availability, human health',
                         'Water availability, terrestrial ecosystem',
                         'Water scarcity'], axis=0, level=0, inplace=True)
        # adding water use to exiobase flows to match with water use from STATCAN physical accounts
        # water use in exiobase is identified through "water withdrawal" and NOT "water consumption"
        adding_water_use = pd.DataFrame(0, index=pd.MultiIndex.from_product([['Water use'], ['m3']]),
                                        columns=self.S_exio.index)
        # STATCAN excluded water use due to hydroelectricity from their accounts, we keep consistency by removing them too
        adding_water_use.loc[:, [i for i in self.S_exio.index if 'Water Withdrawal' in i and (
                'hydro' not in i or 'tide' not in i)]] = 1

        # adding energy use to exiobase flows to match with energy use from STATCAN physical accounts
        # energy use in exiobase is identified through "Energy Carrier Use: Total"
        # note that STATCAN only covers energy use, thus energy supply, loss, etc. flows from exiobase are excluded
        adding_energy_use = pd.DataFrame(0, index=pd.MultiIndex.from_product([['Energy'], ['TJ']]),
                                         columns=self.S_exio.index)
        adding_energy_use.loc[:, [i for i in self.S_exio.index if 'Energy Carrier Use: Total' in i]] = 1
        self.C_exio = pd.concat([self.C_exio, adding_water_use, adding_energy_use])
        # forcing the match with self.C (annoying parentheses for climate change long and short term)
        self.C_exio.index = self.C.index
        self.C_exio = self.C_exio.fillna(0)

        self.methods_metadata = pd.DataFrame(self.C.index.tolist(), columns=['Impact category', 'unit'])
        self.methods_metadata = self.methods_metadata.set_index('Impact category')

        self.balance_flows(concordance)

        self.C = self.C.join(self.C_exio)

    def better_distribution_for_agriculture_ghgs(self):
        """
        GHG physical flow accounts from StatCan only provide the GHG emissions for Crop and animal production aggregated.
        By default, an economic allocation is applied to distribute these emissions to the corresponding sectors.
        However, this is too constraining, as direct emissions vary vastly between animal and crop productions.
        We rely on Exiobase to get a better distribution for GHGs in these sectors.
        :return:
        """

        # separate crops from animal breeding in Exiobase sectors
        crops_exio = ['Paddy rice', 'Wheat', 'Cereal grains nec', 'Vegetables, fruit, nuts', 'Oil seeds',
                      'Sugar cane, sugar beet', 'Plant-based fibers', 'Crops nec']
        animals_exio = ['Cattle', 'Pigs', 'Poultry', 'Meat animals nec', 'Raw milk', 'Wool, silk-worm cocoons']
        # identify the three GHGs that are covered by openIO
        CO2 = [i for i in self.F_exio.index if 'CO2' in i]
        CH4 = [i for i in self.F_exio.index if 'CH4' in i]
        N2O = [i for i in self.F_exio.index if 'N2O' in i]
        # isolate GHG emissions from crop production in Exiobase
        crops_emissions = pd.concat(
            [self.F_exio.loc(axis=1)[:, crops_exio].groupby(axis=1, level=0).sum().loc[CO2].sum(),
             self.F_exio.loc(axis=1)[:, crops_exio].groupby(axis=1, level=0).sum().loc[CH4].sum(),
             self.F_exio.loc(axis=1)[:, crops_exio].groupby(axis=1, level=0).sum().loc[N2O].sum()],
            axis=1)
        crops_emissions.columns = ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide']
        crops_emissions = crops_emissions.loc['CA']
        # isolate GHG emissions from meat production in Exiobase
        meat_emissions = pd.concat(
            [self.F_exio.loc(axis=1)[:, animals_exio].groupby(axis=1, level=0).sum().loc[CO2].sum(),
             self.F_exio.loc(axis=1)[:, animals_exio].groupby(axis=1, level=0).sum().loc[CH4].sum(),
             self.F_exio.loc(axis=1)[:, animals_exio].groupby(axis=1, level=0).sum().loc[N2O].sum()], axis=1)
        meat_emissions.columns = ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide']
        meat_emissions = meat_emissions.loc['CA']
        # get the totals per GHG
        tot_co2 = crops_emissions.loc['Carbon dioxide'] + meat_emissions.loc['Carbon dioxide']
        tot_ch4 = crops_emissions.loc['Methane'] + meat_emissions.loc['Methane']
        tot_n2o = crops_emissions.loc['Dinitrogen monoxide'] + meat_emissions.loc['Dinitrogen monoxide']
        # calculate the distribution, according to Exiobase
        crops_emissions.loc['Carbon dioxide'] /= tot_co2
        crops_emissions.loc['Methane'] /= tot_ch4
        crops_emissions.loc['Dinitrogen monoxide'] /= tot_n2o
        # calculate the distribution, according to Exiobase
        meat_emissions.loc['Carbon dioxide'] /= tot_co2
        meat_emissions.loc['Methane'] /= tot_ch4
        meat_emissions.loc['Dinitrogen monoxide'] /= tot_n2o
        # store it in a single dataframe
        ghgs_exio_distribution = pd.concat([crops_emissions, meat_emissions], axis=1)
        ghgs_exio_distribution.columns = ['Crops', 'Meat']

        # now that we have the distribution of GHG per big sector, we apply this distribution to openIO data
        for ghg in ['Carbon dioxide', 'Methane', 'Dinitrogen monoxide']:

            tot = self.F.loc[[i for i in self.F.index if i[1] == ghg], [i for i in self.F.columns if i[1] in [
                'Crop production (except cannabis, greenhouse, nursery and floriculture production)',
                'Greenhouse, nursery and floriculture production (except cannabis)',
                'Animal production (except aquaculture)', 'Aquaculture']]].groupby(axis=1, level=0).sum()

            crops = self.F.loc[[i for i in self.F.index if i[1] == ghg], [i for i in self.F.columns if i[1] in [
                'Crop production (except cannabis, greenhouse, nursery and floriculture production)',
                'Greenhouse, nursery and floriculture production (except cannabis)']]]

            animals = self.F.loc[[i for i in self.F.index if i[1] == ghg], [i for i in self.F.columns if i[1] in [
                'Animal production (except aquaculture)', 'Aquaculture']]]

            for province in tot.columns:
                tot_prod_crop_and_meat_province = tot.loc[[(province, ghg, 'Air')], province].iloc[0]

                exio_crop_distrib = ghgs_exio_distribution.loc[ghg, 'Crops']
                crops.loc[[(province, ghg, 'Air')]] = (crops.loc[[(province, ghg, 'Air')]] /
                                                       crops.loc[[(province, ghg, 'Air')]].sum().sum() *
                                                       exio_crop_distrib * tot_prod_crop_and_meat_province)
                self.F.loc[[i for i in self.F.index if i[1] == ghg and i[0] == province], [
                    i for i in self.F.columns if i[1] in [
                        'Crop production (except cannabis, greenhouse, nursery and floriculture production)',
                        'Greenhouse, nursery and floriculture production (except cannabis)']]] = crops.loc[[(province, ghg, 'Air')]]

                exio_animal_distrib = ghgs_exio_distribution.loc[ghg, 'Meat']
                animals.loc[[(province, ghg, 'Air')]] = (animals.loc[[(province, ghg, 'Air')]] /
                                                         animals.loc[[(province, ghg, 'Air')]].sum().sum() *
                                                         exio_animal_distrib * tot_prod_crop_and_meat_province)

                self.F.loc[[i for i in self.F.index if i[1] == ghg and i[0] == province], [
                    i for i in self.F.columns if i[1] in [
                        'Animal production (except aquaculture)','Aquaculture']]] = animals.loc[[(province, ghg, 'Air')]]

    def differentiate_country_names_openio_exio(self):
        """
        Some province names are identical to country names in exiobase (e.g., 'SK' and 'NL'). So we changed province
        names to, e.g., 'CA-SK'.
        :return:
        """

        self.A.index = (pd.MultiIndex.from_product([['CA-' + i for i in self.matching_dict.keys()],
                                                    [i[1] for i in self.commodities]]).tolist() +
                        self.A_exio.index.tolist())
        self.A.index = pd.MultiIndex.from_tuples(self.A.index)
        self.A.columns = self.A.index
        self.Y.index = self.A.index
        self.Y.columns = [('CA-' + i[0], i[1], i[2]) for i in self.Y.columns]
        self.Y.columns = pd.MultiIndex.from_tuples(self.Y.columns)
        self.R.columns = pd.MultiIndex.from_product([['CA-' + i for i in self.matching_dict.keys()],
                                                     [i[1] for i in self.commodities]]).tolist()
        self.R.columns = pd.MultiIndex.from_tuples(self.R.columns)
        self.R.index = [('CA-' + i[0], i[1]) for i in self.R.index]
        self.R.index = pd.MultiIndex.from_tuples(self.R.index)
        self.W.columns = [('CA-' + i[0], i[1]) for i in self.W.columns]
        self.W.columns = pd.MultiIndex.from_tuples(self.W.columns)
        self.W.index = [('CA-' + i[0], i[1]) for i in self.W.index]
        self.W.index = pd.MultiIndex.from_tuples(self.W.index)
        self.WY.columns = [('CA-' + i[0], i[1], i[2]) for i in self.WY.columns]
        self.WY.columns = pd.MultiIndex.from_tuples(self.WY.columns)
        self.WY.index = [('CA-' + i[0], i[1]) for i in self.WY.index]
        self.WY.index = pd.MultiIndex.from_tuples(self.WY.index)
        self.U.columns = [('CA-' + i[0], i[1]) for i in self.U.columns]
        self.U.columns = pd.MultiIndex.from_tuples(self.U.columns)
        self.U.index = [('CA-' + i[0], i[1]) for i in self.U.index]
        self.U.index = pd.MultiIndex.from_tuples(self.U.index)
        self.V.columns = [('CA-' + i[0], i[1]) for i in self.V.columns]
        self.V.columns = pd.MultiIndex.from_tuples(self.V.columns)
        self.V.index = [('CA-' + i[0], i[1]) for i in self.V.index]
        self.V.index = pd.MultiIndex.from_tuples(self.V.index)
        self.F.columns = [('CA-' + i[0], i[1]) for i in self.F.columns]
        self.F.columns = pd.MultiIndex.from_tuples(self.F.columns)
        self.F.index = [('CA-' + i[0], i[1], i[2]) if type(i) == tuple else i for i in self.F.index]
        self.minerals.columns = [('CA-' + i[0], i[1]) for i in self.minerals.columns]
        self.minerals.columns = pd.MultiIndex.from_tuples(self.minerals.columns)
        self.FY.columns = [('CA-' + i[0], i[1], i[2]) for i in self.FY.columns]
        self.FY.columns = pd.MultiIndex.from_tuples(self.FY.columns)
        self.FY.index = [('CA-' + i[0], i[1], i[2]) if type(i) == tuple else i for i in self.FY.index]
        self.C.columns = [('CA-' + i[0], i[1], i[2]) if type(i) == tuple else i for i in self.C.columns]
        self.g.index = [('CA-' + i[0], i[1]) for i in self.g.index]
        self.g.columns = [('CA-' + i[0], i[1]) for i in self.g.columns]
        self.g.index = pd.MultiIndex.from_tuples(self.g.index)
        self.g.columns = pd.MultiIndex.from_tuples(self.g.columns)
        self.inv_g.columns = self.g.columns
        self.inv_g.index = self.g.columns
        self.q.index = [('CA-' + i[0], i[1]) for i in self.q.index]
        self.q.columns = [('CA-' + i[0], i[1]) for i in self.q.columns]
        self.q.index = pd.MultiIndex.from_tuples(self.q.index)
        self.q.columns = pd.MultiIndex.from_tuples(self.q.columns)
        self.inv_q.columns = self.q.index
        self.inv_q.index = self.q.index

    def refine_meat_sector(self):
        """
        Because the meat sector is aggregated into one sector, the economic allocation from the technology-industry
        construct creates some issues. For instance, the Quebec products of cattle sector is composed of 85% purchases
        of Hogs, because Quebec mainly produces Hogs and not cattle. So, we refine the definition of these meat sectors
        by forcing the products of cattle sectors to only buy Cattle and not Hogs.
        :return:
        """

        meat_transfo = ['Fresh and frozen beef and veal', 'Fresh and frozen pork',
                        'Fresh and frozen poultry of all types',
                        'Products of meat cattle', 'Products of meat pigs', 'Products of meat poultry']
        meat_breeding = ['Cattle and calves', 'Hogs', 'Poultry', 'Pigs', 'Cattle']

        for province in ['CA-AB', 'CA-BC', 'CA-MB', 'CA-NB', 'CA-NL', 'CA-NS', 'CA-NT',
                         'CA-NU', 'CA-ON', 'CA-PE', 'CA-QC', 'CA-SK', 'CA-YT']:
            meat_sector = 'Fresh and frozen beef and veal'
            total_meat_breeding = self.A.loc(axis=0)[:, meat_breeding].loc(axis=1)[province, meat_sector].sum()
            total_meat_transfo = self.A.loc(axis=0)[:, meat_transfo].loc(axis=1)[province, meat_sector].sum()
            # rescale meat_sector
            if self.A.loc[[i for i in self.A.index if i[1] in ['Cattle and calves', 'Cattle']],
                          [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum().sum() != 0:
                self.A.loc[[i for i in self.A.index if i[1] in ['Cattle and calves', 'Cattle']],
                           [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] /= (
                        self.A.loc[[i for i in self.A.index if i[1] in ['Cattle and calves', 'Cattle']],
                                   [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum() /
                        total_meat_breeding)
            if self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen beef and veal',
                                                               'Products of meat cattle']],
                          [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum().sum() != 0:
                self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen beef and veal',
                                                                'Products of meat cattle']],
                           [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] /= (
                        self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen beef and veal',
                                                                        'Products of meat cattle']],
                                   [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum() /
                        total_meat_transfo)
            # remove other meats
            self.A.loc[[i for i in self.A.index if i[1] in ['Pigs',
                                                            'Hogs',
                                                            'Poultry',
                                                            'Fresh and frozen pork',
                                                            'Fresh and frozen poultry of all types',
                                                            'Products of meat pigs',
                                                            'Products of meat poultry']],
                       [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] = 0

            meat_sector = 'Fresh and frozen pork'
            total_meat_breeding = self.A.loc(axis=0)[:, meat_breeding].loc(axis=1)[province, meat_sector].sum()
            total_meat_transfo = self.A.loc(axis=0)[:, meat_transfo].loc(axis=1)[province, meat_sector].sum()
            # rescale meat_sector
            if self.A.loc[[i for i in self.A.index if i[1] in ['Pigs', 'Hogs']],
                          [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum().sum() != 0:
                self.A.loc[[i for i in self.A.index if i[1] in ['Pigs', 'Hogs']],
                           [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] /= (
                        self.A.loc[[i for i in self.A.index if i[1] in ['Pigs', 'Hogs']],
                                   [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum() /
                        total_meat_breeding)
            if self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen pork',
                                                               'Products of meat pigs']],
                          [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum().sum() != 0:
                self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen pork',
                                                                'Products of meat pigs']],
                           [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] /= (
                        self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen pork',
                                                                        'Products of meat pigs']],
                                   [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum() /
                        total_meat_transfo)
            # remove other meats
            self.A.loc[[i for i in self.A.index if i[1] in ['Cattle and calves',
                                                            'Cattle',
                                                            'Poultry',
                                                            'Fresh and frozen beef and veal',
                                                            'Fresh and frozen poultry of all types',
                                                            'Products of meat cattle',
                                                            'Products of meat poultry']],
                       [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] = 0

            meat_sector = 'Fresh and frozen poultry of all types'
            total_meat_breeding = self.A.loc(axis=0)[:, meat_breeding].loc(axis=1)[province, meat_sector].sum()
            total_meat_transfo = self.A.loc(axis=0)[:, meat_transfo].loc(axis=1)[province, meat_sector].sum()
            # rescale meat_sector
            if self.A.loc[[i for i in self.A.index if i[1] in ['Poultry']],
                          [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum().sum() != 0:
                self.A.loc[[i for i in self.A.index if i[1] in ['Poultry']],
                           [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] /= (
                        self.A.loc[[i for i in self.A.index if i[1] in ['Poultry']],
                                   [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum() /
                        total_meat_breeding)
            if self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen poultry of all types',
                                                               'Products of meat poultry']],
                          [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum().sum() != 0:
                self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen poultry of all types',
                                                                'Products of meat poultry']],
                           [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] /= (
                        self.A.loc[[i for i in self.A.index if i[1] in ['Fresh and frozen poultry of all types',
                                                                        'Products of meat poultry']],
                                   [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]].sum() /
                        total_meat_transfo)

            # remove other meats
            self.A.loc[[i for i in self.A.index if i[1] in ['Cattle and calves',
                                                            'Cattle',
                                                            'Pigs',
                                                            'Hogs',
                                                            'Fresh and frozen beef and veal',
                                                            'Fresh and frozen pork',
                                                            'Products of meat cattle',
                                                            'Products of meat pigs']],
                       [i for i in self.A.columns if (i[0] == province and i[1] == meat_sector)]] = 0

    def normalize_flows(self):
        """
        Produce normalized environmental extensions
        :return: self.S and self.F with product classification if it's been selected
        """

        if self.classification == 'industry':
            self.S = self.F.dot(self.inv_g)

        if self.classification == 'product':
            self.F = self.F.dot(self.V.dot(self.inv_g).T)
            self.F = pd.concat([self.F, self.minerals])
            self.S = self.F.dot(self.inv_q)
            self.S = pd.concat([self.S, self.S_exio]).fillna(0)
            self.S = self.S.reindex(self.A.columns, axis=1)
            # change provinces metadata for S here
            self.S.columns = self.A.columns

        # adding empty flows to FY to allow multiplication with self.C
        self.FY = pd.concat([pd.DataFrame(0, self.F.index, self.Y.columns), self.FY])
        self.FY = self.FY.groupby(self.FY.index).sum()
        self.FY = self.FY.reindex(self.C.columns).fillna(0)

        self.emission_metadata = pd.concat([self.emission_metadata, self.unit_exio])

    def differentiate_biogenic_carbon_emissions(self):
        """
        The physical flow GHG accounts from StatCan do not differentiate between CO2 fossil and biogenic. We thus use
        exiobase biogenic vs fossil CO2 distribution per sector to determine the amount of CO2 biogenic in StatCan
        data.
        :return:
        """

        # identify biogenic and fossil CO2 emissions in Exiobase
        CO2_fossil = [i for i in self.F_exio.index if 'CO2' in i and 'biogenic' not in i and 'peat decay' not in i]
        CO2_bio = [i for i in self.F_exio.index if 'CO2' in i and 'biogenic' in i or 'peat decay' in i]
        CO2 = [i for i in self.F_exio.index if 'CO2' in i]

        # loading concordances between exiobase classification and IOIC
        ioic_exio = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/IOIC_EXIOBASE.xlsx'),
                                  'commodities')
        ioic_exio = ioic_exio[2:].drop('IOIC Detail level - EXIOBASE', axis=1).set_index('Unnamed: 1').fillna(0)
        ioic_exio.index.name = None
        ioic_exio.index = [{j[0]: j[1] for j in self.commodities}[i] for i in ioic_exio.index]
        ioic_exio /= ioic_exio.sum()
        ioic_exio = ioic_exio.fillna(0)

        # apply the distribution of biogenic CO2 from Exiobase to openIO sectors
        bio = self.F_exio.loc[CO2_bio, 'CA'].dot(ioic_exio.T).sum() / self.F_exio.loc[CO2, 'CA'].dot(
            ioic_exio.T).sum()
        bio = bio.fillna(0)
        bio = pd.DataFrame(pd.concat([bio] * len([i for i in self.S.columns.levels[0] if 'CA-' in i])), columns=[
            'Carbon dioxide - biogenic'])
        bio.index = [i for i in self.S.columns if 'CA-' in i[0]]
        bio_openio = self.S.loc[[i for i in self.S.index if 'Carbon dioxide' == i[1]],
                                [i for i in self.S.columns if 'CA-' in i[0]]].copy()
        bio_openio = np.multiply(bio_openio, bio.iloc[:, 0])
        bio_openio.index = [(i[0], 'Carbon dioxide - biogenic', i[2]) for i in bio_openio.index]

        # apply the distribution of fossil CO2 from Exiobase to openIO sectors
        fossil = self.F_exio.loc[CO2_fossil, 'CA'].dot(ioic_exio.T).sum() / self.F_exio.loc[CO2, 'CA'].dot(
            ioic_exio.T).sum()
        fossil = fossil.fillna(0)
        fossil = pd.DataFrame(pd.concat([fossil] * len([i for i in self.S.columns.levels[0] if 'CA-' in i])), columns=[
            'Carbon dioxide - fossil'])
        fossil.index = [i for i in self.S.columns if 'CA-' in i[0]]
        fossil_openio = self.S.loc[[i for i in self.S.index if 'Carbon dioxide' == i[1]],
                                   [i for i in self.S.columns if 'CA-' in i[0]]].copy()
        fossil_openio = np.multiply(fossil_openio, fossil.iloc[:, 0])
        fossil_openio.index = [(i[0], 'Carbon dioxide - fossil', i[2]) for i in fossil_openio.index]

        # drop total CO2 emissions
        self.S.drop([i for i in self.S.index if 'Carbon dioxide' == i[1]], inplace=True)
        # add fossil and biogenic CO2 emissions
        self.S = pd.concat([self.S, fossil_openio.reindex(self.S.columns, axis=1).fillna(0),
                            bio_openio.reindex(self.S.columns, axis=1).fillna(0)])

        # same story for self.F
        bio_openio_scaled = self.F.loc[[i for i in self.F.index if 'Carbon dioxide' == i[1]],
                                       [i for i in self.F.columns if 'CA-' in i[0]]].copy()
        bio_openio_scaled = np.multiply(bio_openio_scaled, bio.iloc[:, 0])
        bio_openio_scaled.index = [(i[0], 'Carbon dioxide - biogenic', i[2]) for i in bio_openio_scaled.index]
        bio_openio_scaled = bio_openio_scaled.fillna(0)
        fossil_openio_scaled = self.F.loc[[i for i in self.F.index if 'Carbon dioxide' == i[1]],
                                          [i for i in self.F.columns if 'CA-' in i[0]]].copy()
        fossil_openio_scaled = np.multiply(fossil_openio_scaled, fossil.iloc[:, 0])
        fossil_openio_scaled.index = [(i[0], 'Carbon dioxide - fossil', i[2]) for i in fossil_openio_scaled.index]
        fossil_openio_scaled = fossil_openio_scaled.fillna(0)

        self.F.drop([i for i in self.F.index if 'Carbon dioxide' == i[1]], inplace=True)
        self.F = pd.concat([self.F, fossil_openio_scaled.reindex(self.F.columns, axis=1).fillna(0),
                            bio_openio_scaled.reindex(self.F.columns, axis=1).fillna(0)])

        # and now create biogenic and fossil rows for self.FY
        self.FY.index = [(i[0], 'Carbon dioxide - fossil', i[2]) if i[1] == 'Carbon dioxide' else i for i in
                         self.FY.index]

        # add "fossil" to the elementary flow name in characterization matrix
        self.C.columns = [(i[0], 'Carbon dioxide - fossil', i[2]) if i[1] == 'Carbon dioxide' else i for i in
                          self.C.columns]

        # also add an entry for biogenic carbon in characterization matrix
        provinces = [i for i in self.A.columns.levels[0] if 'CA-' in i]
        for province in provinces:
            self.C.loc[:, [(province, 'Carbon dioxide - biogenic', 'Air')]] = 0

        # reindex stuff around
        self.F = self.F.reindex(self.C.columns).fillna(0)
        self.F = self.F.reindex(self.A.index, axis=1).fillna(0)
        self.FY = self.FY.reindex(self.F.index).fillna(0)

    def calc(self):
        """
        Method to calculate the Leontief inverse and get total impacts
        :return: self.L (total requirements), self.E (total emissions), self.D (total impacts)
        """
        I = pd.DataFrame(np.eye(len(self.A)), self.A.index, self.A.columns)
        self.L = pd.DataFrame(np.linalg.solve(I - self.A, I), self.A.index, I.columns)
        self.E = self.S.dot(self.L).dot(self.Y) + self.FY
        self.D = self.C.dot(self.E)

# -------------------------------------------------- SUPPORT ----------------------------------------------------------

    def balance_flows(self, concordance):
        """
        Some flows from the NPRI trigger some double counting if left unattended. This method deals with these flows
        :return: balanced self.F
        """

        # we want to use handy multi-index features so we remove flows without multi-index and plug them back at the end
        F_multiindex = self.F.loc[[i for i in self.F.index if type(i) == tuple]].copy()
        F_multiindex.index = pd.MultiIndex.from_tuples(F_multiindex.index)

        # VOCs
        rest_of_voc = [i for i in concordance.index if 'Speciated VOC' in i and concordance.loc[i].isna().iloc[0]]
        df = F_multiindex.loc[[i for i in F_multiindex.index if i[1] in rest_of_voc]]

        try:
            F_multiindex.loc[:, 'Volatile organic compounds', 'Air'] += df.groupby(level=0).sum().values
        except KeyError:
            # name changed in 2018 version
            F_multiindex.loc(axis=0)[:, 'Volatile Organic Compounds (VOCs)', 'Air'] += df.groupby(level=0).sum().values

        F_multiindex.drop(F_multiindex.loc(axis=0)[:, rest_of_voc].index, inplace=True)
        # adjust characterization matrix too
        self.C = self.C.drop([i for i in self.C.columns if i[1] in rest_of_voc], axis=1)

        if self.year >= 2018:
            # PMs, only take highest value flow as suggested by the NPRI team:
            # [https://www.canada.ca/en/environment-climate-change/services/national-pollutant-release-inventory/using-interpreting-data.html]
            for sector in F_multiindex.columns:
                little_pm = F_multiindex.loc[
                    (sector[0], 'PM2.5 - Particulate Matter <= 2.5 Micrometers', 'Air'), sector]
                big_pm = F_multiindex.loc[(sector[0], 'PM10 - Particulate Matter <= 10 Micrometers', 'Air'), sector]
                unknown_size = F_multiindex.loc[(sector[0], 'Total particulate matter', 'Air'), sector]
                if little_pm >= big_pm:
                    if little_pm >= unknown_size:
                        F_multiindex.loc[(sector[0], 'PM10 - Particulate Matter <= 10 Micrometers', 'Air'), sector] = 0
                        F_multiindex.loc[(sector[0], 'Total particulate matter', 'Air'), sector] = 0
                    else:
                        F_multiindex.loc[(sector[0], 'PM10 - Particulate Matter <= 10 Micrometers', 'Air'), sector] = 0
                        F_multiindex.loc[
                            (sector[0], 'PM2.5 - Particulate Matter <= 2.5 Micrometers', 'Air'), sector] = 0
                else:
                    if big_pm > unknown_size:
                        F_multiindex.loc[
                            (sector[0], 'PM2.5 - Particulate Matter <= 2.5 Micrometers', 'Air'), sector] = 0
                        F_multiindex.loc[(sector[0], 'Total particulate matter', 'Air'), sector] = 0
                    else:
                        F_multiindex.loc[(sector[0], 'PM10 - Particulate Matter <= 10 Micrometers', 'Air'), sector] = 0
                        F_multiindex.loc[
                            (sector[0], 'PM2.5 - Particulate Matter <= 2.5 Micrometers', 'Air'), sector] = 0
        else:
            # PMs, only take highest value flow as suggested by the NPRI team:
            # [https://www.canada.ca/en/environment-climate-change/services/national-pollutant-release-inventory/using-interpreting-data.html]
            for sector in F_multiindex.columns:
                little_pm = F_multiindex.loc[(sector[0], 'PM2.5', 'Air'), sector]
                big_pm = F_multiindex.loc[(sector[0], 'PM10', 'Air'), sector]
                unknown_size = F_multiindex.loc[(sector[0], 'Total particulate matter', 'Air'), sector]
                if little_pm >= big_pm:
                    if little_pm >= unknown_size:
                        F_multiindex.loc[(sector[0], 'PM10', 'Air'), sector] = 0
                        F_multiindex.loc[(sector[0], 'Total particulate matter', 'Air'), sector] = 0
                    else:
                        F_multiindex.loc[(sector[0], 'PM10', 'Air'), sector] = 0
                        F_multiindex.loc[(sector[0], 'PM2.5', 'Air'), sector] = 0
                else:
                    if big_pm > unknown_size:
                        F_multiindex.loc[(sector[0], 'PM2.5', 'Air'), sector] = 0
                        F_multiindex.loc[(sector[0], 'Total particulate matter', 'Air'), sector] = 0
                    else:
                        F_multiindex.loc[(sector[0], 'PM10', 'Air'), sector] = 0
                        F_multiindex.loc[(sector[0], 'PM2.5', 'Air'), sector] = 0

        # plug back the non multi-index flows
        self.F = pd.concat([F_multiindex, self.F.loc[[i for i in self.F.index if type(i) != tuple]].copy()])

    def split_private_public_sectors(self, NAICS_code, IOIC_code):
        """
        Support method to split equally emissions from private and public sectors
        :param NAICS_code: [string or list] the NAICS code(s) whose emissions will be split
        :param IOIC_code: [string] the IOIC_code inhereting the split emissions (will be private or public sector)
        :return: updated self.F
        """
        df = self.F.loc(axis=1)[:, NAICS_code].copy()
        if type(NAICS_code) == list:
            df.columns = pd.MultiIndex.from_product([self.matching_dict, [IOIC_code] * len(NAICS_code)])
        elif type(NAICS_code) == str:
            df.columns = pd.MultiIndex.from_product([self.matching_dict, [IOIC_code]])
        self.F = pd.concat([self.F, df / 2], axis=1)
        self.F.loc(axis=1)[:, NAICS_code] /= 2

    def produce_npri_iw_concordance_file(self):
        """
        Method to obtain the NPRI_IW_concordance.xlsx file (for reproducibility)
        :return: the NPRI_IW_concordance.xlsx file
        """

        IW = pd.read_excel(
            pkg_resources.resource_stream(
                __name__, '/Data/IW+ 1_48 EP_1_30 MP_as DB_rules_compliance_with_manually_added_CF.xlsx'))

        df = IW.set_index('CAS number')
        df.index = [str(i) for i in df.index]
        df = df.groupby(df.index).head(n=1)

        concordance_IW = dict.fromkeys(self.F.index.levels[0])

        # match pollutants using CAS numbers
        for pollutant in concordance_IW:
            match_CAS = ''
            try:
                if len(self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number'].split('-')[0]) == 2:
                    match_CAS = '0000' + self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number']
                elif len(self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number'].split('-')[0]) == 3:
                    match_CAS = '000' + self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number']
                elif len(self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number'].split('-')[0]) == 4:
                    match_CAS = '00' + self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number']
                elif len(self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number'].split('-')[0]) == 5:
                    match_CAS = '0' + self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number']
                elif len(self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number'].split('-')[0]) == 6:
                    match_CAS = self.emission_metadata.loc[(pollutant, 'Air'), 'CAS Number']
                try:
                    concordance_IW[pollutant] = [df.loc[i, 'Elem flow name'] for i in df.index if i == match_CAS][0]

                except IndexError:
                    pass
            except KeyError:
                pass

        # hardcoding what could not be matched using CAS number
        concordance_IW['Ammonia (total)'] = 'Ammonia'
        concordance_IW['Fluorine'] = 'Fluorine'
        concordance_IW['PM10 - Particulate matter <=10 microns'] = 'Particulates, < 10 um'
        concordance_IW['PM2.5 - Particulate matter <=2.5 microns'] = 'Particulates, < 2.5 um'
        concordance_IW['Total particulate matter'] = 'Particulates, unspecified'
        concordance_IW['Speciated VOC - Cycloheptane'] = 'Cycloheptane'
        concordance_IW['Speciated VOC - Cyclohexene'] = 'Cyclohexene'
        concordance_IW['Speciated VOC - Cyclooctane'] = 'Cyclooctane'
        concordance_IW['Speciated VOC - Hexane'] = 'Hexane'
        concordance_IW[
            'Volatile organic compounds'] = 'NMVOC, non-methane volatile organic compounds, unspecified origin'

        # proxies, NOT A 1 FOR 1 MATCH but better than no characterization factor
        concordance_IW['HCFC-123 (all isomers)'] = 'Ethane, 2-chloro-1,1,1,2-tetrafluoro-, HCFC-123'
        concordance_IW['HCFC-124 (all isomers)'] = 'Ethane, 2,2-dichloro-1,1,1-trifluoro-, HCFC-124'
        concordance_IW['Nonylphenol and its ethoxylates'] = 'Nonylphenol'
        concordance_IW['Phosphorus (yellow or white only)'] = 'Phosphorus'
        concordance_IW['Phosphorus (total)'] = 'Phosphorus'
        concordance_IW['PAHs, total unspeciated'] = 'Hydrocarbons, aromatic'
        concordance_IW['Aluminum oxide (fibrous forms only)'] = 'Aluminium'
        concordance_IW['Antimony (and its compounds)'] = 'Antimony'
        concordance_IW['Arsenic (and its compounds)'] = 'Arsenic'
        concordance_IW['Cadmium (and its compounds)'] = 'Cadmium'
        concordance_IW['Chromium (and its compounds)'] = 'Chromium'
        concordance_IW['Hexavalent chromium (and its compounds)'] = 'Chromium VI'
        concordance_IW['Cobalt (and its compounds)'] = 'Cobalt'
        concordance_IW['Copper (and its compounds)'] = 'Copper'
        concordance_IW['Lead (and its compounds)'] = 'Lead'
        concordance_IW['Nickel (and its compounds)'] = 'Nickel'
        concordance_IW['Mercury (and its compounds)'] = 'Mercury'
        concordance_IW['Manganese (and its compounds)'] = 'Manganese'
        concordance_IW['Selenium (and its compounds)'] = 'Selenium'
        concordance_IW['Silver (and its compounds)'] = 'Silver'
        concordance_IW['Thallium (and its compounds)'] = 'Thallium'
        concordance_IW['Zinc (and its compounds)'] = 'Zinc'
        concordance_IW['Speciated VOC - Butane  (all isomers)'] = 'Butane'
        concordance_IW['Speciated VOC - Butene  (all isomers)'] = '1-Butene'
        concordance_IW['Speciated VOC - Anthraquinone (all isomers)'] = 'Anthraquinone'
        concordance_IW['Speciated VOC - Decane  (all isomers)'] = 'Decane'
        concordance_IW['Speciated VOC - Dodecane  (all isomers)'] = 'Dodecane'
        concordance_IW['Speciated VOC - Heptane  (all isomers)'] = 'Heptane'
        concordance_IW['Speciated VOC - Nonane  (all isomers)'] = 'Nonane'
        concordance_IW['Speciated VOC - Octane  (all isomers)'] = 'N-octane'
        concordance_IW['Speciated VOC - Pentane (all isomers)'] = 'Pentane'
        concordance_IW['Speciated VOC - Pentene (all isomers)'] = '1-Pentene'

        return pd.DataFrame.from_dict(concordance_IW, orient='index')

    def export(self, filepath='', format=''):
        """
        Function to export in the chosen format.
        :param filepath: the path where to store the export file
        :param format: available formats 'csv', 'excel', 'pickle', 'json'
        :return: nothing
        """

        if not filepath:
            print("Please provide a filepath")
            return
        if not format:
            print("Please enter a format")
            return

        def flat_multiindex(df):
            df.index = df.index.tolist()
            df.columns = df.columns.tolist()
        flat_multiindex(self.A)
        flat_multiindex(self.Y)
        flat_multiindex(self.R)
        flat_multiindex(self.S)
        flat_multiindex(self.FY)
        flat_multiindex(self.C)

        def remove_zeros(df):
            return df.replace({0: np.nan})
        self.A = remove_zeros(self.A)
        self.Y = remove_zeros(self.Y)
        self.R = remove_zeros(self.R)
        self.S = remove_zeros(self.S)
        self.FY = remove_zeros(self.FY)
        self.C = remove_zeros(self.C)

        if format == 'excel':
            writer = pd.ExcelWriter(filepath, engine='xlsxwriter')

            self.A.to_excel(writer, 'A')
            self.Y.to_excel(writer, 'Y')
            self.R.to_excel(writer, 'R')
            self.S.to_excel(writer, 'S')
            self.FY.to_excel(writer, 'FY')
            self.C.to_excel(writer, 'C')

            writer.save()

        else:
            print('Format requested not implemented yet.')

# ------------------------------------------------ DEPRECATED ---------------------------------------------------------
    def deprecated_province_import_export(self, province_trade_file):
        """
        Method extracting and formatting inter province imports/exports
        :return: modified self.U, self.V, self.W, self.Y
        """

        province_trade_file = province_trade_file

        province_trade_file.Origin = [{v: k for k, v in self.matching_dict.items()}[i.split(') ')[1]] if (
                    ')' in i and i != '(81) Canadian territorial enclaves abroad') else i for i in
                                    province_trade_file.Origin]
        province_trade_file.Destination = [{v: k for k, v in self.matching_dict.items()}[i.split(') ')[1]] if (
                    ')' in i and i != '(81) Canadian territorial enclaves abroad') else i for i in
                                         province_trade_file.Destination]
        # extracting and formatting supply for each province
        province_trade = pd.pivot_table(data=province_trade_file, index='Destination', columns=['Origin', 'Product'])

        province_trade = province_trade.loc[
            [i for i in province_trade.index if i in self.matching_dict], [i for i in province_trade.columns if
                                                                                i[1] in self.matching_dict]]
        province_trade *= 1000
        province_trade.columns = [(i[1], i[2].split(': ')[1]) if ':' in i[2] else i for i in
                                     province_trade.columns]
        province_trade.drop([i for i in province_trade.columns if i[1] not in [i[1] for i in self.commodities]],
                            axis=1, inplace=True)
        province_trade.columns = pd.MultiIndex.from_tuples(province_trade.columns)
        for province in province_trade.index:
            province_trade.loc[province, province] = 0

        for importing_province in province_trade.index:
            U_Y = pd.concat([self.U.loc[importing_province, importing_province],
                             self.Y.loc[importing_province, importing_province]], axis=1)
            total_imports = province_trade.groupby(level=1,axis=1).sum().loc[importing_province]
            index_commodity = [i[1] for i in self.commodities]
            total_imports = total_imports.reindex(index_commodity).fillna(0)
            initial_distribution = ((U_Y.T / (U_Y.sum(axis=1))) * total_imports).T.fillna(0)

            # Remove changes in inventories as imports will not go directly into this category
            initial_distribution.drop(["Changes in inventories"], axis=1, inplace=True)
            U_Y.drop(["Changes in inventories"], axis=1, inplace=True)
            # imports cannot be allocated to negative gross fixed capital formation as it is probably not importing if
            # it's transferring ownership for a given product
            initial_distribution.loc[initial_distribution.loc[:, 'Gross fixed capital formation'] < 0,
                                     'Gross fixed capital formation'] = 0
            U_Y.loc[U_Y.loc[:, 'Gross fixed capital formation'] < 0, 'Gross fixed capital formation'] = 0

            # Remove products where total imports exceed consumption, or there are actually no imports
            bad_ix_excess_imports = total_imports[(U_Y.sum(1) - total_imports) < 0].index.to_list()
            bad_ix_no_import = total_imports[total_imports <= 0].index.to_list()
            bad_ix = bad_ix_excess_imports + bad_ix_no_import
            initial_distribution = initial_distribution.drop(bad_ix, axis=0)
            U_Y = U_Y.drop(bad_ix, axis=0)
            total_imports = total_imports.drop(bad_ix)

            # pyomo optimization (see code at the end)
            Ui, S_imports, S_positive = reconcile_entire_region(U_Y, initial_distribution, total_imports)

            # add index entries that are null
            Ui = Ui.reindex([i[1] for i in self.commodities]).fillna(0)

            # remove really small values (< 1$) coming from optimization
            Ui = Ui[Ui > 1].fillna(0)

            # distribution balance imports to the different exporting regions
            final_demand_imports = [i for i in Ui.columns if i not in self.U.columns.levels[1]]
            for exporting_province in province_trade.index:
                if importing_province != exporting_province:
                    df = ((Ui.T * (province_trade / province_trade.sum()).fillna(0).loc[
                        exporting_province, importing_province]).T).reindex(Ui.index).fillna(0)
                    # assert index and columns are the same before using .values
                    assert all(self.U.loc[exporting_province, importing_province].index == df.loc[:,
                                                                                             self.U.columns.levels[
                                                                                                 1]].reindex(
                        self.U.loc[exporting_province, importing_province].columns, axis=1).index)
                    assert all(self.U.loc[exporting_province, importing_province].columns == df.loc[:,
                                                                                               self.U.columns.levels[
                                                                                                   1]].reindex(
                        self.U.loc[exporting_province, importing_province].columns, axis=1).columns)
                    # assign new values into self.U and self.Y
                    self.U.loc[exporting_province, importing_province] = df.loc[:,
                                                                           self.U.columns.levels[1]].reindex(
                        self.U.loc[exporting_province, importing_province].columns, axis=1).values
                    self.Y.loc[exporting_province, importing_province].update(df.loc[:, final_demand_imports])

            # remove inter-provincial trade from intra-provincial trade
            self.U.loc[importing_province, importing_province].update(
                self.U.loc[importing_province, importing_province] - self.U.loc[
                    [i for i in self.matching_dict if i != importing_province], importing_province].groupby(
                    level=1).sum())
            self.Y.loc[importing_province, importing_province].update(
                self.Y.loc[importing_province, importing_province] - self.Y.loc[
                    [i for i in self.matching_dict if i != importing_province], importing_province].groupby(
                    level=1).sum())

    def deprecated_match_ghg_accounts_to_iots(self):
        """
        Method was for aggregated GHG accounts. New method works with disaggregated accounts.

        Method matching GHG accounts to IOIC classification selected by the user
        :return: self.F and self.FY with GHG flows included
        """
        GHG = pd.read_csv(pkg_resources.resource_stream(__name__, '/Data/GHG_emissions.csv'))
        GHG = GHG.loc[[i for i in GHG.index if GHG.REF_DATE[i] == self.year and GHG.GEO[i] != 'Canada']]
        # kilotonnes to kg CO2e
        GHG.VALUE *= 1000000

        FD_GHG = GHG.loc[[i for i in GHG.index if GHG.Sector[i] == 'Total, households']]
        FD_GHG.GEO = [{v: k for k, v in self.matching_dict.items()}[i] for i in FD_GHG.GEO]
        FD_GHG = FD_GHG.pivot_table(values='VALUE', index=['GEO', 'Sector'])
        FD_GHG.columns = [('', 'GHGs', '')]
        FD_GHG.index.names = (None, None)
        FD_GHG.index = pd.MultiIndex.from_product([self.matching_dict, ['Household final consumption expenditure']])

        GHG = GHG.loc[[i for i in GHG.index if '[' in GHG.Sector[i]]]
        GHG.Sector = [i.split('[')[1].split(']')[0] for i in GHG.Sector]
        GHG.GEO = [{v: k for k, v in self.matching_dict.items()}[i] for i in GHG.GEO]
        GHG = GHG.pivot_table(values='VALUE', index=['GEO', 'Sector'])
        GHG.columns = [('', 'GHGs', '')]
        GHG.index.names = (None, None)
        # reindex to have the same number of sectors covered per province
        GHG = GHG.reindex(pd.MultiIndex.from_product([self.matching_dict, GHG.index.levels[1]])).fillna(0)
        # removing the fictive sectors
        GHG.drop([i for i in GHG.index if re.search(r'^FC', i[1])], inplace=True)

        concordance = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Concordances/GHG_concordance.xlsx'),
                                    self.level_of_detail)
        concordance.set_index('GHG codes', inplace=True)

        if self.level_of_detail in ['Summary level', 'Link-1961 level']:
            # transform GHG accounts sectors to IOIC sectors
            GHG.index = pd.MultiIndex.from_tuples([(i[0], concordance.loc[i[1], 'IOIC']) for i in GHG.index])
            # some sectors are not linked to IOIC (specifically weird Canabis sectors), drop them
            if len([i for i in GHG.index if type(i[1]) == float]) != 0:
                GHG.drop([i for i in GHG.index if type(i[1]) == float], inplace=True)
            # grouping emissions from same sectors
            GHG = GHG.groupby(GHG.index).sum()
            GHG.index = pd.MultiIndex.from_tuples(GHG.index)
            # reindex to make sure dataframe is ordered as in dictionary
            GHG = GHG.reindex(pd.MultiIndex.from_product([self.matching_dict, [i[0] for i in self.industries]]))
            # switching codes for readable names
            GHG.index = pd.MultiIndex.from_product([self.matching_dict, [i[1] for i in self.industries]])

            # spatializing GHG emissions in case we later regionalize impacts (even though it's useless for climate change)
            GHG = pd.concat([GHG] * len(GHG.index.levels[0]), axis=1)
            GHG.columns = pd.MultiIndex.from_product([self.matching_dict, ['GHGs'], ['Air']])
            # emissions takes place in the province of the trade
            for province in GHG.index.levels[0]:
                GHG.loc[province, [i for i in GHG.index.levels[0] if i != province]] = 0
            # add GHG emissions to other pollutants
            self.F = pd.concat([self.F, GHG.T])
            self.F.index = pd.MultiIndex.from_tuples(self.F.index)

        elif self.level_of_detail in ['Link-1997 level', 'Detail level']:
            # dropping empty sectors (mostly Cannabis related)
            to_drop = concordance.loc[concordance.loc[:, 'IOIC'].isna()].index
            concordance.drop(to_drop, inplace=True)
            ghgs = pd.DataFrame()
            for code in concordance.index:
                # L97 and D levels are more precise than GHG accounts, we use market share to distribute GHGs
                sectors_to_split = [i[1] for i in self.industries if
                                    i[0] in concordance.loc[code].dropna().values.tolist()]
                output_sectors_to_split = self.V.loc[:,
                                          [i for i in self.V.columns if i[1] in sectors_to_split]].sum()
                share_sectors_to_split = pd.Series(0, output_sectors_to_split.index)
                for province in output_sectors_to_split.index.levels[0]:
                    share_sectors_to_split.loc[province] = ((output_sectors_to_split.loc[province] /
                                                             output_sectors_to_split.loc[province].sum()).fillna(
                        0).values) * GHG.loc(axis=0)[:, code].loc[province].iloc[0, 0]
                ghgs = pd.concat([ghgs, share_sectors_to_split])
            ghgs.index = pd.MultiIndex.from_tuples(ghgs.index)
            ghgs.columns = pd.MultiIndex.from_product([[''], ['GHGs'], ['Air']])

            # spatializing GHG emissions
            ghgs = pd.concat([ghgs] * len(ghgs.index.levels[0]), axis=1)
            ghgs.columns = pd.MultiIndex.from_product([self.matching_dict, ['GHGs'], ['Air']])
            for province in ghgs.columns.levels[0]:
                ghgs.loc[[i for i in ghgs.index.levels[0] if i != province], province] = 0
            # adding GHG accounts to pollutants
            self.F = pd.concat([self.F, ghgs.T])
            # reindexing
            self.F = self.F.reindex(self.U.columns, axis=1)

        # GHG emissions for households
        self.FY = pd.DataFrame(0, FD_GHG.columns, self.Y.columns)
        self.FY.update(FD_GHG.T)
        # spatializing them too
        self.FY = pd.concat([self.FY] * len(GHG.index.levels[0]))
        self.FY.index = pd.MultiIndex.from_product([self.matching_dict, ['GHGs'], ['Air']])
        for province in self.FY.columns.levels[0]:
            self.FY.loc[[i for i in self.FY.columns.levels[0] if i != province], province] = 0

        self.emission_metadata.loc['GHGs', 'CAS Number'] = 'N/A'
        self.emission_metadata.loc['GHGs', 'Unit'] = 'kgCO2eq'

    def deprecated_international_import_export(self):
        """
        Method executes two things:
        1. It removes international imports from the use table
        2. It estimates the emissions (or the impacts) from these international imports, based on exiobase
        Resulting emissions are stored in self.SL_INT
        :returns self.SL_INT, modified self.U
        """

        # 1. Removing international imports

        # aggregating international imports in 1 column
        self.INT_imports = self.INT_imports.groupby(axis=1, level=1).sum()
        # need to flatten multiindex for the concatenation to work properly
        self.Y.columns = self.Y.columns.tolist()
        self.U.columns = self.U.columns.tolist()
        # concat U and Y to look at all users (industry + final demand)
        U_Y = pd.concat([self.U, self.Y], axis=1)
        # negative values represent sells, so it does not make sense to rebalance imports with them
        U_Y = U_Y[U_Y > 0].fillna(0)
        # weighted average of who is requiring the international imports, based on national use
        self.who_uses_int_imports = (U_Y.T / U_Y.sum(1)).T * self.INT_imports.values
        # remove international imports from national use
        self.U = self.U - self.who_uses_int_imports.reindex(self.U.columns, axis=1)
        # check that nothing fuzzy is happening with negative values that are not due to artefacts
        assert len(self.U[self.U < -1].dropna(how='all', axis=1).dropna(how='all', axis=0)) == 0
        # remove negative artefacts (like 1e-10$)
        self.U = self.U[self.U > 0].fillna(0)
        assert not self.U[self.U < 0].any().any()
        # remove international imports from final demand
        self.Y = self.Y - self.who_uses_int_imports.reindex(self.Y.columns, axis=1)
        # remove negative artefacts
        self.Y = pd.concat([self.Y[self.Y >= 0].fillna(0), self.Y[self.Y < -1].fillna(0)], axis=1)
        self.Y = self.Y.groupby(by=self.Y.columns, axis=1).sum()
        self.Y.columns = pd.MultiIndex.from_tuples(self.Y.columns)

        # 2. Estimating the emissions of international imports

        # importing exiobase
        io = pymrio.parse_exiobase3(self.exiobase_folder)

        # selecting the countries which make up the international imports
        INT_countries = [i for i in io.get_regions().tolist() if i != 'CA']

        # importing the concordance between open IO and exiobase classifications
        ioic_exio = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/IOIC_EXIOBASE.xlsx'),
                                  'commodities')
        # make concordance on codes because Statcan changes names of sectors with updates
        ioic_exio = ioic_exio[2:].drop('IOIC Detail level - EXIOBASE', axis=1).set_index('Unnamed: 1').fillna(0)
        ioic_exio.index.name = None

        # we create the matrix which represents the interactions of the openIO-Canada model with the exiobase model
        self.link_openio_exio = pd.DataFrame(0, io.A.index,
                                 pd.MultiIndex.from_product([self.matching_dict, [i[0] for i in self.commodities]]))

        # this matrix is populated using the market distribution according to exiobase
        for product in self.link_openio_exio.columns:
            if len(ioic_exio.loc[product[1]][ioic_exio.loc[product[1]] == 1].index) != 0:
                df = io.x.loc(axis=0)[:, ioic_exio.loc[product[1]][ioic_exio.loc[product[1]] == 1].index]
                df = df.loc[INT_countries] / df.loc[INT_countries].sum()
                self.link_openio_exio.loc[:, product].update((io.A.reindex(df.index, axis=1).dot(df)).iloc[:, 0])

        # index the link matrices properly
        self.link_openio_exio.columns = pd.MultiIndex.from_product([self.matching_dict, [i[1] for i in self.commodities]])

        # self.link_openio_exio is currently in euros and includes the value added from exiobase
        # we thus rescale on 1 euro (excluding value added from exiobase) and then convert to CAD (hence the 1.5)
        self.link_openio_exio = (self.link_openio_exio/self.link_openio_exio.sum()/1.5).fillna(0)

        # save the quantity of imported goods by sectors of openIO-Canada
        self.IMP_matrix = self.who_uses_int_imports.reindex(self.U.columns, axis=1)

        # save the matrices from exiobse before deleting them to save space
        self.A_exio = io.A.copy()
        self.S_exio = io.satellite.S.copy()
        # millions euros to euros
        self.S_exio.iloc[9:] /= 1000000
        # convert euros to canadian dollars
        self.S_exio /= 1.5
        del io.A
        del io.satellite.S

    def deprecated_balance_model(self):
        """
        Balance the system so that the financial balance is kept. Also concatenate openIO with Exiobase.
        :return:
        """

        # rescale self.link_openio_exio columns sum to match what is actually imported according to openIO-Canada
        link_A = self.link_openio_exio.dot(self.IMP_matrix.fillna(0))
        # concat international trade with interprovincial trade
        self.A = pd.concat([self.A, link_A])
        # provinces from openIO-Canada are not allowed to trade with the Canada region from exiobase
        self.A.loc['CA'] = 0
        # concat openIO-Canada with exiobase to get the full technology matrix
        df = pd.concat([pd.DataFrame(0, index=self.A.columns, columns=self.A_exio.columns), self.A_exio])
        self.A = pd.concat([self.A, df], axis=1)

        # same exercise for final demand
        link_Y = self.link_openio_exio.dot(self.who_uses_int_imports.reindex(self.Y.columns, axis=1).fillna(0))
        # concat interprovincial and international trade for final demands
        self.Y = pd.concat([self.Y, link_Y])
        # provinces from openIO-Canada are not allowed to trade with the Canada region from exiobase
        self.Y.loc['CA'] = 0

    def deprecated_load_merchandise_international_trade_database_industry(self):
        """
        Loading and treating the international trade merchandise database of Statistics Canada.
        Original source: https://open.canada.ca/data/en/dataset/cf26a8f3-bf96-4fd3-8fa9-e0b4089b5866
        :return:
        """

        # load the merchandise international trade database from the openIO files
        merchandise_database = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/Imports.xlsx'))
        merchandise_database.country = merchandise_database.country.ffill()
        # load concordances between NAICS and IOIC to match the merch database to openIO sectors
        conc = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/NAICS-IOIC.xlsx'))
        # match the two product classifications
        imports_industry_classification = merchandise_database.merge(conc, left_on="NAICS",
                                                                     right_on="NAICS 6 Code").loc[:,
                                          ['country', str(self.year), 'IOIC']]
        imports_industry_classification = imports_industry_classification.set_index(['country', 'IOIC']).sort_index()
        # country names also need to be matched with Exiobase countries
        with open(pkg_resources.resource_filename(__name__, "Data/country_concordance_imports.json"), 'r') as f:
            country_conc = json.load(f)
        # match the two country classifications
        imports_industry_classification.index = [(country_conc[i[0]], i[1]) for i in imports_industry_classification.index]
        imports_industry_classification.index = pd.MultiIndex.from_tuples(imports_industry_classification.index)
        # groupby to add all Rest-of-the-World regions together (i.e., WE, WF, WA, WL, WM)
        imports_industry_classification = imports_industry_classification.groupby(imports_industry_classification.index).sum()
        imports_industry_classification.index = pd.MultiIndex.from_tuples(imports_industry_classification.index)
        # change industry codes for industry names
        imports_industry_classification.index = [(i[0], {i[0]: i[1] for i in self.industries}[i[1]]) for i in imports_industry_classification.index]
        imports_industry_classification.index = pd.MultiIndex.from_tuples(imports_industry_classification.index)
        # drop Canada as we consider there cannot be international imports from Canada by Canada (?!?)
        self.imports_industry_classification = imports_industry_classification.drop('CA', axis=0, level=0)

    def deprecated_link_merchandise_database_to_openio_industry(self):
        """
        Linking the international trade merchandise database of Statistics Canada to openIO-Canada.
        :return:
        """

        # first, the merchandise database is in industry classification, we change it to commodity classification
        industry_to_commodity = self.inv_g.dot(self.V.T).dot(self.inv_q).groupby(axis=0, level=1).sum().groupby(axis=1,
                                                                                                                level=1).sum()
        imports_commodity_classification = pd.DataFrame()
        for region in self.imports_industry_classification.index.levels[0].drop('CA'):
            df = self.imports_industry_classification.T.loc[:, region].reindex(industry_to_commodity.index, axis=1).fillna(0).dot(
                industry_to_commodity)
            df.columns = pd.MultiIndex.from_product([[region], df.columns])
            imports_commodity_classification = pd.concat([imports_commodity_classification, df], axis=1)

        # the absolute values of imports_commodity_classification do not mean a thing
        # we only use those to calculate a weighted average of imports per country
        for product in imports_commodity_classification.columns.levels[1]:
            total = imports_commodity_classification.loc(axis=1)[:, product].sum(1)
            for region in imports_commodity_classification.columns.levels[0]:
                imports_commodity_classification.loc(axis=1)[region, product] /= total

        imports_commodity_classification = imports_commodity_classification.dropna(axis=1).T

        # now we link the merchandise trade data to importation values given in the supply & use tables
        self.merchandise_international_imports = pd.DataFrame()

        df = self.who_uses_int_imports.groupby(axis=0, level=1).sum()
        df = pd.concat([df] * len(imports_commodity_classification.index.levels[0]))
        df.index = pd.MultiIndex.from_product(
            [imports_commodity_classification.index.levels[0], self.who_uses_int_imports.index.levels[1]])

        for product in imports_commodity_classification.index.levels[1]:
            try:
                # if KeyError -> sector is not covered by merchandise trade data (i.e., service)
                dff = (df.loc(axis=0)[:, product].T * imports_commodity_classification.loc(axis=0)[:, product].iloc[:, 0]).T
                self.merchandise_international_imports = pd.concat([self.merchandise_international_imports, dff])
            except KeyError:
                pass

        self.merchandise_international_imports = self.merchandise_international_imports.sort_index()

        # check that all covered imports are distributed correctly in the imp_commodity_scale dataframes
        assert np.isclose(self.merchandise_international_imports.sum().sum(),
                          self.who_uses_int_imports.loc(axis=0)[:,
                          self.merchandise_international_imports.index.levels[1]].sum().sum())

    def deprecated_link_international_trade_data_to_exiobase_industry(self):
        """
        Linking the data from the international merchandise trade database, which was previously linked to openIO-Canada,
        to exiobase.

        Some links fail because of the transformation from industry classification to product. For instance, "Aviation
        fuel" is produced from "Petroleum refineries" and "Basic chemicals manufacturing". When importing "Basic
        chemicals manufacturing" from Luxembourg, a portion of that import is thus considered being "Aviation fuel".
        And yet according to Exiobase, Luxembourg does not produce any fuel (no refineries in the country). Inconsistent
        values like this were stored in a dictionary. These values SHOULD be redistributed to the different other
        industries (i.e., to other products from "Basic chemicals manufacturing"). However the total value of these
        inconsistent imports only represents 6,339,722 CAD, that is, 0.0008% of total import values. So they were just
        ignored.
        :return:
        """

        # loading Exiobase
        io = pymrio.parse_exiobase3(self.exiobase_folder)

        # save the matrices from exiobase because we need them later
        self.A_exio = io.A.copy()
        self.S_exio = io.satellite.S.copy()
        # millions euros to euros
        self.S_exio.iloc[9:] /= 1000000
        # convert euros to canadian dollars
        self.S_exio /= 1.5

        # loading concordances between exiobase classification and IOIC
        ioic_exio = pd.read_excel(pkg_resources.resource_stream(__name__, '/Data/IOIC_EXIOBASE.xlsx'),
                                  'commodities')
        ioic_exio = ioic_exio[2:].drop('IOIC Detail level - EXIOBASE', axis=1).set_index('Unnamed: 1').fillna(0)
        ioic_exio.index.name = None
        ioic_exio.index = [{j[0]: j[1] for j in self.commodities}[i] for i in ioic_exio.index]

        # determine the Canadian imports according to Exiobase
        canadian_imports_exio = io.A.loc[:, 'CA'].sum(1).drop('CA', axis=0, level=0)

        # link to exiobase
        link_openio_exio = pd.DataFrame()
        not_traded = {}

        for merchandise in self.merchandise_international_imports.index.levels[1]:
            # check if there is trading happening for the uncovered commodity or not
            if self.who_uses_int_imports.groupby(axis=0, level=1).sum().loc[merchandise].sum() != 0:
                # 1 for 1 with exiobase -> easy
                if ioic_exio.loc[merchandise].sum() == 1:
                    exio_sector = ioic_exio.loc[merchandise][ioic_exio.loc[merchandise] == 1].index[0]
                    dff = self.merchandise_international_imports.loc(axis=0)[:, merchandise]
                    dff.index = [(i[0], exio_sector) for i in dff.index]
                    link_openio_exio = pd.concat([link_openio_exio, dff])
                # 1 for many with exiobase -> headscratcher
                elif ioic_exio.loc[merchandise].sum() > 1:
                    exio_sector = ioic_exio.loc[merchandise][ioic_exio.loc[merchandise] == 1].index.tolist()
                    dff = self.merchandise_international_imports.loc(axis=0)[:, merchandise].copy()
                    dff = pd.concat([dff] * len(exio_sector))
                    dff = dff.sort_index()
                    dff.index = pd.MultiIndex.from_product([dff.index.levels[0], exio_sector])
                    for region in dff.index.levels[0]:
                        dfff = (dff.loc[region].T *
                                (canadian_imports_exio.loc(axis=0)[region, exio_sector] /
                                 canadian_imports_exio.loc(axis=0)[region, exio_sector].sum()).loc[region]).T
                        # if our calculations shows imports (e.g., fertilizers from Bulgaria) for a product but there
                        # are not seen in exiobase, then we rely on io.x to distribute between commodities
                        if not np.isclose(
                                self.merchandise_international_imports.loc(axis=0)[:,
                                merchandise].loc[region].sum().sum(), dfff.sum().sum()):
                            dfff = (dff.loc[region].T *
                                    (io.x.loc(axis=0)[region, exio_sector].iloc[:, 0] /
                                     io.x.loc(axis=0)[region, exio_sector].iloc[:, 0].sum()).loc[region]).T
                        # if the product is simply not produced at all by the country according to exiobase, isolate the value in a dict
                        if not np.isclose(dff.loc[region].iloc[0].sum(), dfff.sum().sum()):
                            not_traded[(region, merchandise)] = [exio_sector, dff.loc[region].iloc[0].sum()]
                        dfff.index = pd.MultiIndex.from_product([[region], dfff.index])
                        link_openio_exio = pd.concat([link_openio_exio, dfff])
                        link_openio_exio.index = pd.MultiIndex.from_tuples(link_openio_exio.index)
                else:
                    print(merchandise + ' is not linked to any Exiobase sector!')

        link_openio_exio.index = pd.MultiIndex.from_tuples(link_openio_exio.index)
        link_openio_exio = link_openio_exio.groupby(link_openio_exio.index).sum()
        link_openio_exio.index = pd.MultiIndex.from_tuples(link_openio_exio.index)
        link_openio_exio = link_openio_exio.reindex(io.A.index).fillna(0)

        # the marchendise database only covers imports of merchandise. For services we rely on Exiobase imports
        covered = list(set([i[1] for i in self.merchandise_international_imports.index]))
        uncovered = [i for i in [j[1] for j in self.commodities] if i not in covered]

        df = self.who_uses_int_imports.groupby(axis=0, level=1).sum()
        df = pd.concat([df] * len(self.imports_industry_classification.index.levels[0].drop('CA')))
        df.index = pd.MultiIndex.from_product(
            [self.imports_industry_classification.index.levels[0].drop('CA'), self.who_uses_int_imports.index.levels[1]])

        for sector in uncovered:
            # check if there is trading happening for the uncovered commodity or not
            if self.who_uses_int_imports.groupby(axis=0, level=1).sum().loc[sector].sum() != 0:
                # 1 for 1 with exiobase -> easy
                if ioic_exio.loc[sector].sum() == 1:
                    exio_sector = ioic_exio.loc[sector][ioic_exio.loc[sector] == 1].index[0]
                    dff = canadian_imports_exio.loc(axis=0)[:, exio_sector]
                    dff.index = df.loc(axis=0)[:, sector].index
                    dff = (df.loc(axis=0)[:, sector].T * dff / dff.sum()).T
                    dff.index = pd.MultiIndex.from_product([dff.index.levels[0], [exio_sector]])
                    link_openio_exio.loc[dff.index] += dff
                    assert np.isclose(self.who_uses_int_imports.groupby(axis=0, level=1).sum().loc[sector].sum(),
                                      dff.sum().sum())
                # 1 for many with exiobase -> headscratcher
                else:
                    exio_sector = ioic_exio.loc[sector][ioic_exio.loc[sector] == 1].index.tolist()
                    dff = pd.concat([df.loc(axis=0)[:, sector]] * len(exio_sector))
                    dff.index = pd.MultiIndex.from_product([df.index.levels[0], exio_sector])
                    dff = dff.sort_index()
                    dff = (dff.T * (canadian_imports_exio.loc(axis=0)[:, exio_sector] /
                                    canadian_imports_exio.loc(axis=0)[:, exio_sector].sum()).sort_index()).T
                    # if the product is simply not produced at all by the country according to exiobase, isolate the value in a dict
                    if not np.isclose(dff.loc[region].iloc[0].sum(), dff.sum().sum()):
                        not_traded[(region, merchandise)] = [exio_sector, dff.loc[region].iloc[0].sum()]
                    link_openio_exio.loc[dff.index] += dff

        # distribute the link matrix between industries and final demands
        self.link_openio_exio_technosphere = link_openio_exio.reindex(self.U.columns, axis=1)
        self.link_openio_exio_final_demands = link_openio_exio.reindex(self.Y.columns, axis=1)

        # normalize the international imports for the technology matrix
        self.link_openio_exio_technosphere = self.link_openio_exio_technosphere.dot(self.inv_g.dot(self.V.T)).dot(self.inv_q)

        # check financial balance is respected before converting to euros
        assert (self.A.sum() + self.R.sum() + self.link_openio_exio_technosphere.sum())[
                   (self.A.sum() + self.R.sum() + self.link_openio_exio_technosphere.sum()) < 0.999].sum() == 0

        # convert from CAD to EURO
        self.link_openio_exio_technosphere /= 1.5
        self.link_openio_exio_final_demands /= 1.5


def todf(data):
    """ Simple function to inspect pyomo element as Pandas DataFrame"""
    try:
        out = pd.Series(data.get_values())
    except AttributeError:
        # probably already is a dataframe
        out = data

    if out.index.nlevels > 1:
        out = out.unstack()
    return out


def treatment_import_data(original_file_path):
    """Function used to treat the merchandise imports trade database file. FIle is way too big to be provided to
    users through Github, so we treat the data to only keep what is relevant."""

    # load database
    merchandise_database = pd.read_csv(original_file_path)
    # drop useless columns

    merchandise_database = merchandise_database.drop(['YearMonth/AnnéeMois', 'Province', 'State/État',
                                                      'Quantity/Quantité', 'Unit of Measure/Unité de Mesure'],
                                                     axis=1)

    # drop international imports coming from Canada
    merchandise_database = merchandise_database[merchandise_database['Country/Pays'] != 'CA']

    # also drop nan countries for obvious reasons
    merchandise_database = merchandise_database.dropna(subset=['Country/Pays'])

    # set the index as country/code multi-index
    merchandise_database = merchandise_database.set_index(['Country/Pays', 'HS6'])

    # regroup data from several months into a single yearly data
    merchandise_database = merchandise_database.groupby(merchandise_database.index).sum()

    # multi-index is cleaner
    merchandise_database.index = pd.MultiIndex.from_tuples(merchandise_database.index)

    return merchandise_database


# pyomo optimization functions
def reconcile_one_product_market(uy, u0, imp, penalty_multiplicator):
    opt = SolverFactory('ipopt')

    # Define model and parameter
    model = ConcreteModel()
    model.U0 = u0
    model.UY = uy
    model.imports = imp

    # Large number used as penalty for slack in the objective function.
    # Defined here as a multiplicative of the largest import value in U0.
    # If solver gives a value error, can adjust penalty multiplicator.
    big = model.U0.max() * penalty_multiplicator

    # Define dimensions ("sets") over which to loop
    model.sectors = model.UY.index.to_list()
    model.non_null_sectors = model.U0[model.U0 != 0].index.to_list()

    # When defining our variable Ui, we initialize it close to U0, really gives the solver a break
    def initialize_close_to_U0(model, sector):
        return model.U0[sector]

    model.Ui = Var(model.sectors, domain=NonNegativeReals, initialize=initialize_close_to_U0)

    # Two slack variables to help our solvers reach a feasible solution
    model.slack_total = Var(domain=Reals)
    model.slack_positive = Var(model.sectors, domain=NonNegativeReals)

    # (soft) Constraint 1: (near) conservation of imports, linked to slack_total
    def cons_total(model):
        return sum(model.Ui[i] for i in model.sectors) + model.slack_total == model.imports

    model.constraint_total = Constraint(rule=cons_total)

    # (soft) Constraint 2: sectoral imports (nearly) always smaller than sectoral use
    def cons_positive(model, sector):
        return model.UY.loc[sector] - model.Ui[sector] >= - model.slack_positive[sector]

    model.constraint_positive = Constraint(model.sectors, rule=cons_positive)

    # Objective function
    def obj_minimize(model):
        # Penalty for relatively deviating from initial estimate _and_ for using slack variables
        # Note the use of big
        return sum(
            ((model.U0[sector] - model.Ui[sector]) / model.U0[sector]) ** 2 for sector in model.non_null_sectors) + \
               big * model.slack_total ** 2 + \
               big * sum(model.slack_positive[i] ** 2 for i in model.sectors)

    model.obj = Objective(rule=obj_minimize, sense=minimize)

    # Solve
    sol = opt.solve(model)
    return todf(model.Ui), model.slack_total.get_values()[None], todf(model.slack_positive)


def reconcile_entire_region(U_Y, initial_distribution, total_imports):
    # Dataframe to fill
    Ui = pd.DataFrame(dtype=float).reindex_like(U_Y)

    # Slack dataframes to, for the record
    S_imports = pd.Series(index=total_imports.index, dtype=float)
    S_positive = pd.DataFrame(dtype=float).reindex_like(U_Y)

    # Loop over all products, selecting the market
    for product in initial_distribution.index:
        uy = U_Y.loc[product]
        u0 = initial_distribution.loc[product]
        imp = total_imports[product]
        penalty_multiplicators = [1E10, 1E9, 1E8, 1E7, 1E6, 1E5, 1E4, 1E3, 1E3, 1E2, 10]

        # Loop through penalty functions until the solver (hopefully) succeeds
        for pen in penalty_multiplicators:
            try:
                ui, slack_import, slack_positive = reconcile_one_product_market(uy, u0, imp, pen)
            except ValueError as e:
                if pen == penalty_multiplicators[-1]:
                    raise e
            else:
                break

        # Assign the rebalanced imports to the right market row
        Ui.loc[product, :] = ui

        # commit slack values to history
        S_imports[product] = slack_import
        S_positive.loc[product, :] = slack_positive

    return Ui, S_imports, S_positive

