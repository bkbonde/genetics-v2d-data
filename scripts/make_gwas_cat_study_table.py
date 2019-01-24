#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Ed Mountjoy
#

import sys
import os
import argparse
import pandas as pd
from pprint import pprint
from collections import OrderedDict
import hashlib
from numpy import nan
from functools import reduce

"""
Note to Monday Ed

- You're creating new study IDs for each sub phenotype using the top loci intermediate
- This will then need to be merged to the top loci final table
- Don't need trait code
- This script will be where the LD bug is fixed also
    https://github.com/opentargets/genetics/issues/158

"""


def main():

    # Parse args
    args = parse_args()

    #
    # Load study table from association table ----------------------------------
    #

    # Load study table from top loci table
    cols_to_keep = ['STUDY ACCESSION', 'PUBMEDID', 'DATE', 'JOURNAL', 'STUDY',
    'FIRST AUTHOR', 'DISEASE/TRAIT', 'MAPPED_TRAIT_URI', 'P-VALUE (TEXT)']
    studies = (
        pd.read_csv(args.in_toploci, sep='\t', header=0)
          .loc[:, cols_to_keep]
          .drop_duplicates()
    )

    # Gorupby will drop null 'P-VALUE (TEXT)' fields
    studies['P-VALUE (TEXT)'] = studies['P-VALUE (TEXT)'].fillna('')

    # # Output a table for GWAS Catalog to debug the duplications
    # (
    #     studies.groupby(['STUDY ACCESSION', 'P-VALUE (TEXT)'])
    #            .agg({
    #                   'PUBMEDID': lambda x: x.nunique(),
    #                   'DATE': lambda x: x.nunique(),
    #                   'JOURNAL': lambda x: x.nunique(),
    #                   'STUDY': lambda x: x.nunique(),
    #                   'FIRST AUTHOR': lambda x: x.nunique(),
    #                   'DISEASE/TRAIT': lambda x: x.nunique(),
    #                   'MAPPED_TRAIT_URI': lambda x: x.nunique()
    #                 })
    #            .reset_index()
    #            .to_csv('gwas_cat_duplicated.tsv', sep='\t', index=None)
    # )

    # Remove Sun et al pQTL study
    studies = studies.loc[studies['STUDY ACCESSION'] != 'GCST005806', :]
    # Remove Huang et al IBD study (GWAS Catalog should not have curated this)
    studies = studies.loc[studies['STUDY ACCESSION'] != 'GCST005837', :]

    #
    # Group EFO codes together -------------------------------------------------
    #

    # EFOs can be different for the ('STUDY ACCESSION', 'P-VALUE (TEXT)') pairs.
    # They must be grouped.
    studies['MAPPED_TRAIT_URI'] = studies['MAPPED_TRAIT_URI'].str.split(', ')
    group_cols = ['STUDY ACCESSION', 'PUBMEDID', 'DATE', 'JOURNAL', 'STUDY',
    'FIRST AUTHOR', 'DISEASE/TRAIT', 'P-VALUE (TEXT)']
    studies = (
        studies.groupby(group_cols)
               .MAPPED_TRAIT_URI
               .agg({'trait_efos': sum})
               .reset_index()
    )

    # Clean and convert EFO list back to string, otherwise pandas can't hash it
    studies['trait_efos'] = studies['trait_efos'].apply(clean_efo)

    #
    # Create new trait name and study IDs --------------------------------------
    #

    # Create new trait name from 'DISEASE/TRAIT' and 'P-VALUE (TEXT)'
    studies['trait_combined'] = (
        studies.loc[:, ['DISEASE/TRAIT', 'P-VALUE (TEXT)']]
               .apply(make_new_trait_name, axis=1)
    )

    # Sort and deduplicate
    studies = (
        studies.sort_values(by=['STUDY ACCESSION', 'trait_combined'])
               .drop_duplicates()
    )

    # Create new study_ids from 'STUDY ACCESSION' and 'trait_combined'
    studies['study_id'] = make_new_study_id(
        studies.loc[:, ['STUDY ACCESSION', 'trait_combined']]
    )

    # Output a study ID lookup table for use elsewhere.
    (
        studies.loc[:, ['STUDY ACCESSION', 'P-VALUE (TEXT)', 'study_id']]
               .to_csv(args.out_id_lut, sep='\t', index=None)
    )
    # studies.to_csv('studies.test.tsv', sep='\t', index=None)

    # Drop 'P-VALUE (TEXT)' and deduplicate
    studies = (
        studies.drop('P-VALUE (TEXT)', axis=1)
               .drop_duplicates()
    )

    # Assert that there are no duplicated IDs. Very important as GWAS Catalog
    # does not perform checks on 'P-VALUE (TEXT)' or 'MAPPED_TRAIT_URI'
    # which can introduce errors
    assert (studies.study_id.value_counts() == 1).all(), \
        'Error: There are duplicated study IDs'

    #
    # Load and merge ancestry information --------------------------------------
    #

    # Load ancestry info
    anc = parse_ancestry_info(args.in_ancestries)

    # Merge study and ancestry info
    merged = pd.merge(studies, anc,
                      on='STUDY ACCESSION',
                      how='left')

    # merged['trait_code'] = [make_hash_trait(val) for val in merged['DISEASE/TRAIT']]

    #
    # Process ------------------------------------------------------------------
    #

    # Keep required cols
    cols = OrderedDict([
        # ['STUDY ACCESSION', 'study_accession'],
        ['study_id', 'study_id'],
        ['PUBMEDID', 'pmid'],
        ['DATE', 'pub_date'],
        ['JOURNAL', 'pub_journal'],
        ['STUDY', 'pub_title'],
        ['FIRST AUTHOR', 'pub_author'],
        ['trait_combined', 'trait_reported'],
        ['trait_efos', 'trait_efos'],
        # ['trait_code', 'trait_code'],
        # ['MAPPED_TRAIT', 'trait_mapped'],
        ['ancestry_initial', 'ancestry_initial'],
        ['ancestry_replication', 'ancestry_replication'],
        ['n_initial', 'n_initial'],
        ['n_replication', 'n_replication']
    ])
    df = merged.rename(columns=cols).loc[:, cols.values()]

    # We don't have case numbers from GWAS Catalog
    df['n_cases'] = nan

    # Add prefix to PMIDs
    df['pmid'] = 'PMID:' + df['pmid'].astype(str)

    # Remove rows where n_initial == nan, these are old studies and their data is
    # inconsistent with the rest of GWAS Catalog (correspondance w Annalisa Buniello)
    df = df.loc[~pd.isnull(df.n_initial), :]

    # Write
    df.to_csv(args.outf, sep='\t', index=None)

def clean_efo(l, sep=';'):
    ''' Takes a list of EFO URIs, cleans them and returns a string
    Params:
        l (list)
    Returns:
        str
    '''
    try:
        return sep.join(set([v.split('/')[-1] for v in l]))
    except TypeError:
        return nan

def make_new_study_id(df):
    ''' Creates a unique study ID from the origin STUDY ACCESSION for each
        new trait
    Params:
        df (pd.df): dataframe with 2 columns: Study accession, trait name
    Returns:
        list of strs
    '''
    cache = {}
    new_ids = []
    for accession, trait in df.values.tolist():

        # Add missing accession to cache
        if not accession in cache:
            cache[accession] = {'counter': 0}

        # Add missing trait to cache
        if not trait in cache[accession]:
            cache[accession]['counter'] += 1
            cache[accession][trait] = cache[accession]['counter']

        # Create new id
        new_id = '{0}_{1}'.format(accession, cache[accession][trait])
        new_ids.append(new_id)

    return new_ids

def make_new_trait_name(row):
    ''' Takes 'DISEASE/TRAIT' and 'P-VALUE (TEXT)' to combine them into a
        single string
    Params:
        row (pd.Series): A single row from pd.df.apply(axis=1)
    Returns:
        string
    '''
    if row['P-VALUE (TEXT)'] == '':
        return row['DISEASE/TRAIT']
    else:
        return '{0} [{1}]'.format(
            row['DISEASE/TRAIT'],
            row['P-VALUE (TEXT)'].strip('()') )

# def make_hash_trait(traitlabel):
#     '''creates an id for each GWAS trait with
#     low probability of collision
#     '''
#     hasher = hashlib.md5(traitlabel.encode('utf-8').lower())
#     # truncating it in half gives 64 bit, which for ~10000 traits makes the collision probability < 1e-7
#     return 'GT_' + hasher.hexdigest()[0:16]

def parse_ancestry_info(inf):
    ''' Parse the GWAS Catalog ancestry info file to get 1 row per study
    Args:
        inf (str): GWAS Catalog ancestry file
    Returns:
        pandas df
    '''
    anc_dict = {}
    df = pd.read_csv(inf, sep='\t', header=0, index_col=False)
    grouped = df.groupby(['STUDY ACCCESSION', 'STAGE'])
    for name, group in grouped:
        # Add study id to output dict
        study_id, stage = name
        if not study_id in anc_dict:
            anc_dict[study_id] = {}
        # Make N
        key = 'n_{0}'.format(stage)
        value = int(group['NUMBER OF INDIVDUALS'].sum())
        anc_dict[study_id][key] = str(value)
        # Make ancestry string
        key = 'ancestry_{0}'.format(stage)
        values = []
        for cat, n in zip(group['BROAD ANCESTRAL CATEGORY'], group['NUMBER OF INDIVDUALS']):
            # Fix problem with "Greater Middle Eastern (Middle Eastern, North African or Persian)"
            cat = cat.replace(
                'Greater Middle Eastern (Middle Eastern, North African or Persian)',
                'Greater Middle Eastern (Middle Eastern / North African / Persian)'
            )
            try:
                n = int(n)
            except ValueError:
                n = 0
            values.append('{0}={1}'.format(cat, n))
        value = ';'.join(values)
        anc_dict[study_id][key] = value

    # Make df
    anc_df = pd.DataFrame.from_dict(anc_dict, orient='index')
    anc_df.insert(0, 'STUDY ACCESSION', anc_df.index)

    return anc_df

def parse_args():
    """ Load command line args """
    parser = argparse.ArgumentParser()
    parser.add_argument('--in_toploci', metavar="<str>", type=str, required=True)
    parser.add_argument('--in_ancestries', metavar="<str>", type=str, required=True)
    parser.add_argument('--outf', metavar="<str>", help=("Output"), type=str, required=True)
    parser.add_argument('--out_id_lut', metavar="<str>", help=("Output study ID lut file"), type=str, required=True)
    args = parser.parse_args()
    return args

if __name__ == '__main__':

    main()
