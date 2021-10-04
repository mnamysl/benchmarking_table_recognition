import io
import os
import sys
import glob
import json
import re

from icu import UnicodeString, Transliterator, UTransDirection
from enum import Enum
from lxml import html, etree

import numpy as np
import networkx as nx


SINGLE_VARIANT_EVAL = True
IGNORE_FP_TABLES = False


class ProcMethod(Enum):
    """
    Input method (one of: gt, iais, tabby, abbyy, tabula)
    """

    GT = 'gt'
    IAIS = 'iais'
    Tabby = 'tabby'
    Abbyy = 'abbyy'
    Tabula = 'tabula'
    Camelot = 'camelot'
    Unknown = 'unknown'

    def __str__(self):
        return self.name


class Cell(object):

    def __init__(self, idx=-1, text="", start_row=-1, start_col=-1, end_row=-1, end_col=-1):
        self.text = self._normalize(text)
        self.start_row = int(start_row)
        self.start_col = int(start_col)
        self.end_row = int(end_row)
        self.end_col = int(end_col)
        self.id = idx

    def __nonzero__(self):
        return self.id >= 0

    __bool__=__nonzero__

    def __str__(self):
        return self.text
        
    #def __repr__(self):
    #    return f"Cell({self.id}, {self.text}, {self.start_row}, {self.start_col}, {self.end_row}, {self.end_col})"
     
    def __eq__(self, other):
        if isinstance(other, Cell):
            return self.text == other.text
        else:
            return False
    
    #def __hash__(self):
    #    return hash(self.id) ^ hash(self.text) ^ hash(self.start_row) ^ hash(self.start_col) ^ hash(self.end_row) ^ hash(self.end_col) ^ hash(self.id)
    
    def empty(self):
        return len(self.text) == 0

    def _normalize(self, text: str):
        utext = UnicodeString(text)
        tli = Transliterator.createInstance("NFKD; [:M:] Remove; NFKC", UTransDirection.FORWARD)
        tli.transliterate(utext)
        text = str(utext)

        text = text.encode('ascii', 'ignore').decode('ascii', 'ignore')
        text = text.replace(" ", "").replace("\t", "").replace("\r", "").replace("\n", "")
        return text
    

class AdjRelationDirection(Enum):
    LeftRight = 1
    TopDown = 2


class AdjRelation(object):
    def __init__(self, from_cell: Cell, to_cell: Cell, direction: AdjRelationDirection):
        self.from_cell = from_cell
        self.to_cell = to_cell
        self.direction = direction

    def __str__(self):
        return f"'{str(self.from_cell)}' -> '{str(self.to_cell)}' [{self.direction}]"

    #def __repr__(self):
    #    return f"AdjRelation({self.from_cell}, {self.to_cell}, {str(self.direction)})"
    
    def __eq__(self, other):
        if isinstance(other, AdjRelation):
            return str(self.from_cell) == str(other.from_cell) and str(self.to_cell) == str(other.to_cell) and self.direction == other.direction
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    #def __hash__(self):
    #    return hash(self.from_cell) ^ hash(self.to_cell) ^ hash(self.direction)


class Table(object):
    def __init__(self):
        self.cells = list()
        self.relations = list()
        self.relation_keys = set()
        self.active = False

    def add_cell(self, c):
        self.cells.append(c)

    def add_relation(self, r):
        rel_key = (r.from_cell.id, r.to_cell.id)
        if rel_key in self.relation_keys:
            return
        self.relation_keys.add(rel_key)
        self.relations.append(r)

    def __str__(self):
        #return '\n'.join([str(c) for c in self.cells])
        return str(self.cell_matrix)

    def __len__(self):
        return len(self.relations)

    def _get_column_lengths(self):
        column_lengths = np.zeros(self.cols, dtype=int)
        for row in self.cell_matrix:
            lengths = np.array([len(str(cell)) for cell in row])
            column_lengths = np.maximum(lengths, column_lengths)
        return column_lengths

    def _get_matrix_str(self):
        column_lengths = self._get_column_lengths()
        lines = list()
        lines.append(f"/{'-' * (column_lengths.sum()+self.cols-1)}\\")
        for row_idx in range(self.rows):
            row = self.cell_matrix[row_idx,:]
            cells = [f"{str(cell):^{column_lengths[col_idx]}}" for col_idx, cell in enumerate(row)]
            cells_linearized = '|'.join(cells)
            lines.append(f"|{cells_linearized}|")
            if row_idx < self.rows - 1:
                lines.append(f"|{'-' * (column_lengths.sum()+self.cols-1)}|")
        lines.append(f"\\{'-' * (column_lengths.sum()+self.cols-1)}/")
        return "\n".join(lines)

    def _get_relations_str(self, direction=None):
        return '\n'.join([str(rel) for rel in self.get_relations() if direction == None or direction == rel.direction])
            
    def get_relations(self):
        return self.relations

    def build_cell_matrix(self, record_overlap=False, overlap_log=None, table_idx=-1, verbose=False):
        self.rows = max([c.end_row for c in self.cells]) + 1 if len(self.cells) > 0 else 0
        self.cols = max([c.end_col for c in self.cells]) + 1 if len(self.cells) > 0 else 0

        #if len(self.rows) == 0 or len(self.cols) == 0:
        #    return True

        #self.cell_matrix = np.empty([self.rows, self.cols], dtype = Cell)
        self.cell_matrix = np.full([self.rows, self.cols], Cell(), dtype = Cell)
        
        has_overlapping_cells, table_header_written = False, False
       
        for c in self.cells:
            x0, y0, x1, y1 = c.start_col, c.start_row, c.end_col, c.end_row
            
            if np.any(self.cell_matrix[y0:y1+1, x0:x1+1]):
                has_overlapping_cells = True

                if record_overlap and overlap_log != None:
                    
                    if not table_header_written:
                        print(f"\ttable {table_idx}", file=overlap_log)
                        table_header_written = True

                    print(f"\t\trows: {y0}-{y1} cols: {x0}-{x1}", file=overlap_log)

            self.cell_matrix[y0:y1+1, x0:x1+1] = c
            
        if verbose:
            print(self._get_matrix_str())

        return not has_overlapping_cells
    
    def _get_matrix_elem(self, fixed_idx, variable_idx, direction: AdjRelationDirection):
        """
        Gets the value of a cell from the matrix depending on the raster scan direction.
        """
        if direction == AdjRelationDirection.LeftRight:
            return self.cell_matrix[fixed_idx, variable_idx]
        elif AdjRelationDirection.TopDown:
            return self.cell_matrix[variable_idx, fixed_idx]
        else:
            print(f"ERROR: unknown relation direction: {direction}!")
            exit(-1)

    def _raster_scan(self, direction: AdjRelationDirection):

        if direction == AdjRelationDirection.LeftRight:
            dim0, dim1 = self.rows, self.cols
        elif direction == AdjRelationDirection.TopDown:
            dim1, dim0 = self.rows, self.cols
        else:
            print(f"ERROR: unknown relation direction: {direction}!")
            exit(-1)

        #print(f"dir: {direction} dims=({dim0},{dim1})")

        for idx0 in range(dim0):
            
            idx10 = 0
            while idx10 < dim1-1:
                current_cell = self._get_matrix_elem(idx0, idx10, direction)

                if current_cell == None or current_cell.empty():
                    idx10 += 1
                else:
                    for idx11 in range(idx10+1, dim1):
                        next_cell = self._get_matrix_elem(idx0, idx11, direction)

                        # check whether the cells have the same ID
                        if current_cell.id == next_cell.id:
                            continue

                        if next_cell != None and not next_cell.empty():
                            self.add_relation(AdjRelation(current_cell, next_cell, direction))
                            break
                    
                    idx10 = idx11

    def extract_relations(self, verbose=False):
        
        # left -> right direction
        self._raster_scan(AdjRelationDirection.LeftRight)

        # top -> down direction
        self._raster_scan(AdjRelationDirection.TopDown)

        if verbose:
            print(self._get_relations_str(None))


def print_line(n: int = 50, prefix="", c='-', eval_log=None):
    print(f"{prefix}{c * n}", file=eval_log)


def get_scores_str(tp, fn, fp, precision, recall, f1, f05):
    return f"TP:{tp} FN:{fn} FP:{fp} GT={tp+fn} RES={tp+fp} PRECISION={precision:.4f} RECALL={recall:.4f} F1={f1:.4f} F0.5={f05:.4f}" 

def get_text(node):
    """
    Gets the textual content of an XML node
    """
    return ''.join(node.itertext())


def get_attribute(node, attribute_name, default_value = None):
    """
    Gets an attribute of an XML node
    """
    if attribute_name in node.attrib:
        attribute = node.get(attribute_name)
    else:
        attribute = default_value

    return attribute


def load_complexity_classes(file_path: str, complexities_to_use=[0,1,2], eval_log=None, verbose=False):
    
    print(f"Loading table complexity classes for each file from '{file_path}'; classes: {complexities_to_use}", file=eval_log)
    
    with open(file_path, "r") as f:
        lines = f.readlines()

    tuples = set()

    for line in lines:
        line = line.strip()
        complexity, filename, table_id = line.split()
        basename = os.path.splitext(filename)[0]

        if int(complexity) in complexities_to_use:
            tuples.add((basename, int(table_id)))

    if verbose:
        for t in tuples:
            print(f"{t}", file=eval_log)
        
    print(f"#Tuples(name, table_id): {len(tuples)}'", file=eval_log)

    return tuples


def load_xml_files(dir_path: str, name_pattern = "*-str.xml", multivariant = False, record_overlap = False, 
        method=ProcMethod.IAIS, tuples_to_use=(), eval_log = None):

    tables = dict()
    pattern = f"{dir_path}/**/{name_pattern}"
        
    cnt_loaded, cnt_tables = 0, 0
    for file_path in glob.glob(pattern, recursive=True): 
        
        # PMC4253432_2/PMC4253432_2-str-result.xml
        # PMC4067690-str.xml

        basename = os.path.basename(file_path)

        m = re.match("PMC(\d+)(_(\d+)){0,1}", basename)

        if m:
            file_id = m.group(1)
            file_nr = m.group(3)
            
            if SINGLE_VARIANT_EVAL:
                if file_nr != None:
                    continue

            key = f"PMC{file_id}"

            if method in [ProcMethod.GT, ProcMethod.IAIS, ProcMethod.Tabby, ProcMethod.Unknown]:
                doc = etree.parse(file_path, etree.XMLParser(encoding='utf-8', ns_clean=True, recover=True))                 
                #print(etree.tostring(doc))
                raw_tables = doc.findall(".//table")
                parse_cells = _parse_cells_icdar

            elif method in [ProcMethod.Abbyy]:
                with open(file_path, 'r', encoding='utf-8-sig') as f:
                    file_content = f.read().encode('utf-8')
                    file_content = file_content.decode('utf-8').replace("http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml", "").encode('utf-8')
                doc = etree.XML(file_content, etree.XMLParser(encoding='utf-8', ns_clean=True, recover=True))
                #print(etree.tostring(doc))
                raw_tables = doc.findall(".//block[@blockType='Table']")
                parse_cells = _parse_cells_abbyy

            elif method in [ProcMethod.Tabula]:
                with open(file_path, 'r', encoding='utf-8') as f:
                    try:
                        raw_tables = json.load(f)
                    except:
                        print(f"Failed loading '{file_path}'")
                        print(sys.exc_info())
                        raw_tables = json.loads("[]")
                #print(json.dumps(raw_tables, indent=2))
                parse_cells = _parse_cells_tabula
            elif method in [ProcMethod.Camelot]:
                from zipfile import ZipFile
                zip_file=ZipFile(file_path)
                raw_tables = [json.loads(zip_file.read(name)) for name in zip_file.namelist()]
                #for t in raw_tables:
                #    print(json.dumps(t))
                parse_cells = _parse_cells_camelot

            file_status = True
            
            # add an empty table record if not exists
            if multivariant:
                items = tables.get(key, dict())
                inner_key = f"PMC{file_id}_{file_nr}"
                item = items.get(inner_key, list())
                items[inner_key] = item
                tables[key] = items
            else:
                item = tables.get(key, list())
                tables[key] = item

            overlap_log = io.StringIO("")

            for tab_idx, raw_table in enumerate(raw_tables):
                table = Table()

                cells = parse_cells(raw_table)
                for c in cells:
                    table.add_cell(c)

                file_status &= table.build_cell_matrix(record_overlap=record_overlap, overlap_log=overlap_log, table_idx=tab_idx+1, verbose=False)
        
                table.extract_relations(verbose=False)

                if multivariant:
                    items = tables.get(key, dict())
                    inner_key = f"PMC{file_id}_{file_nr}"
                    item = items.get(inner_key, list())

                    # reset tables with IDs not in tuples_to_use
                    item_key = (f"PMC{file_id}", len(item)+1)
                    table.active = item_key in tuples_to_use
                    
                    item.append(table)
                    items[inner_key] = item
                    tables[key] = items
                    cnt_tables += 1
                else:
                    item = tables.get(key, list())

                    # reset tables with IDs not in tuples_to_use
                    item_key = (f"PMC{file_id}", len(item)+1)
                    table.active = item_key in tuples_to_use

                    item.append(table)
                    tables[key] = item
                    cnt_tables += 1

                #print(f"[OK] name:'{file_path}' key:{key} num_relations={len(table.get_relations())}")

            cnt_loaded += 1
            print(f"[OK] name:'{file_path}' key:{key} num_tables={len(raw_tables)}", file=eval_log)
            
            if record_overlap:
                with open("overlap_py.csv", "a") as f:
                    print(f"{key};{file_status}", file=f)

                if not file_status:
                    with open("overlap_ext.txt", "a") as f:
                        print(f"{key}", file=f)
                        print(overlap_log.getvalue(), file=f)

    print(f"Summary: loaded {cnt_loaded} files and {cnt_tables} tables.", file=eval_log)
    return tables

def _parse_cells_camelot(json_table):
    
    cells = list()
    cell_id = 0

    for row_idx, json_row in enumerate(json_table):
        for col_idx in range(len(json_row)):
            text = json_row[f"{col_idx}"].strip()
            cells.append(Cell(cell_id, text, row_idx, col_idx, row_idx, col_idx))
            cell_id += 1

    return cells


def _parse_cells_tabula(json_table):
    
    cells = list()
    cell_id = 0

    for row_idx, json_row in enumerate(json_table["data"]):
        for col_idx, json_cell in enumerate(json_row):
            text = json_cell["text"].strip()
            cells.append(Cell(cell_id, text, row_idx, col_idx, row_idx, col_idx))
            cell_id += 1

    return cells


def _parse_cells_abbyy(xml_table):
    
    cells = list()
    xml_rows = xml_table.findall(".//row")

    n_rows = len(xml_rows)
    n_cols = 0
    
    # calculate n_cols
    for xml_row in xml_rows:
        xml_cells = xml_row.findall(".//cell")
        n = 0
        for xml_cell in xml_cells:
            col_span = int(get_attribute(xml_cell, "colSpan", default_value = 1))
            n += col_span
        n_cols = max(n_cols, n)

    tmp_cell_matrix = np.zeros((n_rows, n_cols), dtype=int)

    #print(f"{n_rows} x {n_cols}")

    row_idx, cell_id = 0, 0
    for xml_row in xml_rows:
        xml_cells = xml_row.findall(".//cell")
   
        for xml_cell in xml_cells:
            text = get_text(xml_cell).strip()
            col_span = int(get_attribute(xml_cell, "colSpan", default_value = 1))
            row_span = int(get_attribute(xml_cell, "rowSpan", default_value = 1))
            #print(text, type(text), col_span, type(col_span), row_span, type(row_span))
            
            # find start column index
            for i in range(n_cols):
                c = tmp_cell_matrix[row_idx][i]
                if c == 0:
                    col_idx = i
                    break

            y0, y1, x0, x1 = row_idx, row_idx + row_span - 1, col_idx, col_idx + col_span - 1
            tmp_cell_matrix[y0:y1+1, x0:x1+1] = 1
            
            cells.append(Cell(cell_id, text, y0, x0, y1, x1))
            cell_id += 1

        row_idx += 1

    return cells


def _parse_cells_icdar(xml_table):
    
    cells = list()
    xml_cells = xml_table.findall(".//cell")
    #print(len(xml_cells))

    #print(xml_table)
    #print(xml_cells)
    cell_id = 0
    for xml_cell in xml_cells:
        text = get_text(xml_cell)
        start_row = get_attribute(xml_cell, "start-row")
        start_col = get_attribute(xml_cell, "start-col")
        end_row = get_attribute(xml_cell, "end-row")
        end_col = get_attribute(xml_cell, "end-col")
                    
        cells.append(Cell(cell_id, text, start_row, start_col, end_row, end_col))
        cell_id += 1

    return cells


def _calc_f_beta_score(beta, precision, recall):

    beta_sq = beta * beta
    denominator = (beta_sq * precision) + recall

    f_beta = (1 + beta_sq) * precision * recall / denominator if denominator > 0.0 else 0.0
    return f_beta

def _calc_scores(TP, FN, FP):

    TPFP = TP + FP
    TPFN = TP + FN
    precision = TP / TPFP if TPFP > 0.0 else 0.0
    recall = TP / TPFN if TPFN > 0 else 0.0

    prec_rec = precision + recall
    F1 = _calc_f_beta_score(1.0, precision, recall)
    F05 = _calc_f_beta_score(0.5, precision, recall)

    return precision, recall, F1, F05


def _intersection(la: list, lb: list):
    tmp = lb[:]
    cnt_tp, cnt_fn = 0, 0
    for a in la:
        if a in tmp:
            cnt_tp += 1
            tmp.remove(a)
        else:
            cnt_fn += 1

    return cnt_tp, cnt_fn


def _eval_pair(gt_data, res_data, TP, FN, FP, eval_log):

    gt = gt_data.get_relations()
    res = res_data.get_relations()

    if isinstance(gt, set) and isinstance(res, set):
        TP += len(gt & res)
        FN += len(gt - res)
        FP += len(res - gt)
    elif isinstance(gt, (list, tuple)) and isinstance(res, (list, tuple)):
        #gt_set, res_set = set(gt), set(res)
        #TP += len([i for i in res if i in gt_set]) 
        #FN += len([i for i in gt if i not in res_set]) 
        #FP += len([i for i in res if i not in gt_set]) 
        tp, fn = _intersection(gt, res) 
        tp2, fp = _intersection(res, gt)
        assert(tp == tp2)
        TP += tp
        FN += fn
        FP += fp
    else:
       print(f"Unknown types of GT(=gt) or RES(=res) relations!", file=eval_log)
       exit(-1)

    #print(f"TP={TP} FN={FN} FP={FP}")
    if TP+FN != len(gt):
        print(f"ERROR: TP+FN(={TP+FN}) != len(gt)(={len(gt)})", file=eval_log)
    #else:
    #    print(f"OK: TP+FN(={TP+FN}) == len(gt)(={len(gt)})")

    assert(TP+FN == len(gt))
    
    if TP+FP != len(res):
        print(f"ERROR: TP+FP(={TP+FP}) != len(res)(={len(res)})", file=eval_log)
    #else:
    #    print(f"OK: TP+FP(={TP+FP}) == len(res)(={len(res)})")
    
    assert(TP+FP == len(res))

    return TP, FN, FP


def _create_graph(gt_items, res_items, eval_log):

    # init a (bipartite) graph
    G = nx.Graph()
    gt_nodes, res_nodes, edges = set(), set(), set()
    node2item = dict()
    scores = dict()
    
    for gt_idx, gt_item in enumerate(gt_items):
        gt_node = f"gt_{gt_idx}"
        gt_nodes.add(gt_node)
        node2item[gt_node] = gt_item

        #print(gt_item)

        for res_idx, res_item in enumerate(res_items):
            res_node = f"res_{res_idx}"
            res_nodes.add(res_node)
            node2item[res_node] = res_item
            
            pair_TP, pair_FN, pair_FP = _eval_pair(gt_item, res_item, 0, 0, 0, eval_log)
            pair_P, pair_R, pair_F1, pair_F05 = _calc_scores(pair_TP, pair_FN, pair_FP)
            score = pair_F1
            #score = pair_R
            
            if score > 0:
                edges.add((gt_node, res_node, score))
                scores[(gt_node, res_node)] = (pair_TP, pair_FN, pair_FP, pair_P, pair_R, pair_F1, pair_F05)

            #print(f"\t\tedge:  {gt_node} -> {res_node} : {score:.4f}")

    print_line(n=30, prefix="\t\t", eval_log=eval_log)
    
    # create a bipartite graph from the pairs and run maximum weighted matching
    #gt_nodes = sorted(gt_nodes)
    #res_nodes = sorted(res_nodes)
    G.add_nodes_from(gt_nodes, bipartite=0)
    G.add_nodes_from(res_nodes, bipartite=1)
    G.add_weighted_edges_from(edges)
    
    print(f"\t\tgt_nodes:  {gt_nodes}", file=eval_log)
    print(f"\t\tres_nodes: {res_nodes}", file=eval_log)
    #print(f"\t\tedges:     {edges}")

    return G, gt_nodes, res_nodes, node2item, scores

def _eval_pairs_in_file(gt_label, res_label, gt_items, res_items, TP, FN, FP, eval_log):
    
    # calculate all scores for each pair of tables in the GT and RES data
    cnt_gt, cnt_res = len(gt_items), len(res_items)

    print(f"\tMatching '{gt_label}' (cnt={cnt_gt}) with '{res_label}' (cnt={cnt_res}):", file=eval_log) 
    print_line(n=50, prefix='\t', eval_log=eval_log)

    G, gt_nodes, res_nodes, node2item, scores = _create_graph(gt_items, res_items, eval_log)
    #G, gt_nodes, res_nodes, node2item, scores = _create_graph_dummy()

    matches = nx.max_weight_matching(G)
    #print(f"MATCHES:   {matches}")

    print_line(n=30, prefix="\t\t", eval_log=eval_log)

    page_TP, page_FN, page_FP = 0, 0, 0

    for n1, n2 in matches:
        gt_node = n1 if n1.startswith("gt_") else n2
        res_node = n1 if n1.startswith("res_") else n2

        gt_table = node2item[gt_node]

        pair_TP, pair_FN, pair_FP, pair_P, pair_R, pair_F1, pair_F05 = scores[(gt_node, res_node)] 
        print(f"\t\tmatch: {gt_node} -> {res_node} : {get_scores_str(pair_TP, pair_FN, pair_FP, pair_P, pair_R, pair_F1, pair_F05)}",
            file=eval_log)

        if gt_table.active:
            page_TP += pair_TP
            page_FN += pair_FN
            page_FP += pair_FP

        gt_nodes.remove(gt_node)
        res_nodes.remove(res_node)

    #print(f"REMAINING GT nodes:  {gt_nodes}")
    #print(f"REMAINING RES nodes: {res_nodes}")

    if len(node2item) > 0:
        for n in gt_nodes:
            gt_table = node2item[n]
            if gt_table.active:
                fn = len(gt_table)
                print(f"\t\tMatching for '{n}' not found in the results [MISS]! FN += {fn}", file=eval_log)
                page_FN += fn
        
        if not IGNORE_FP_TABLES:
            for n in res_nodes:
                fp = len(node2item[n])
                print(f"\t\tMatching for '{n}' not found in the references [FALSE-ALARM]! FP += {fp}", file=eval_log)
                page_FP += fp
     
    print_line(n=30, prefix="\t\t", eval_log=eval_log)

    page_P, page_R, page_F1, page_F05 = _calc_scores(page_TP, page_FN, page_FP)

    print(f"\t[{res_label}] {get_scores_str(page_TP, page_FN, page_FP, page_P, page_R, page_F1, page_F05)}",
        file=eval_log) 
    
    TP += page_TP
    FN += page_FN
    FP += page_FP

    return TP, FN, FP


def eval_data(gt_files, res_files, res_multivariant=True, eval_log=None):

    TP, FP, FN = 0, 0, 0

    print_line(c='=', n=100, eval_log=eval_log)
    
    for key, gt_items in gt_files.items():
        if key in res_files and len(res_files[key]) > 0:
            res_items = res_files[key]
            print(f"'{key}' found in both references and results. {len(res_items)} candidate(s) found in the results!", file=eval_log)
            print_line(n=50, eval_log=eval_log)
            
            best_precision, best_recall, best_f1, best_tp, best_fn, best_fp, best_f05 = 0, 0, 0, 0, 0, 0, 0
            best_sub_items, best_sub_key = None, None
            best_secondary_score = -1e10

            if res_multivariant:
                for sub_key, sub_items in res_items.items():
                    tp, fn, fp = _eval_pairs_in_file(key, sub_key, gt_items, sub_items, 0, 0, 0, eval_log)
                    prec, rec, f1, f05 = _calc_scores(tp, fn, fp)
                    secondary_score = tp-fp-fn
                    if (f1 > best_f1) or (f1 == best_f1 and secondary_score > best_secondary_score):
                        best_precision, best_recall, best_f1, best_f05, best_tp, best_fn, best_fp = prec, rec, f1, f05, tp, fn, fp
                        best_sub_key, best_sub_items = sub_key, sub_items
                        best_secondary_score = secondary_score

                    print_line(c='-', prefix='\t', eval_log=eval_log)
            else:
                best_sub_key = key
                best_tp, best_fn, best_fp = _eval_pairs_in_file(key, best_sub_key, gt_items, res_items, 0, 0, 0, eval_log)
                best_precision, best_recall, best_f1, best_f05 = _calc_scores(best_tp, best_fn, best_fp)

                print_line(c='-', prefix='\t', eval_log=eval_log)

            TP, FN, FP = TP + best_tp, FN + best_fn, FP +  best_fp
            print(f"[{key}] best candidate: '{best_sub_key}' {get_scores_str(best_tp, best_fn, best_fp, best_precision, best_recall, best_f1, best_f05)}",
                file=eval_log)

            with open("res_py.csv", "a") as f:
                #print(f"{key};{best_tp+best_fn};{best_tp+best_fp};{best_tp}", file=f)
                print(f"{key};{best_tp};{best_fn};", file=f)

            print_line(c='-', eval_log=eval_log)
            del res_files[key]
        else:
            fn = 0
            for item in gt_items:
                if item.active:
                    n = len(item)
                    fn += n

            with open("res_py.csv", "a") as f:
                print(f"{key};{fn};{0};{0}", file=f)
            
            print(f"'{key}' not found in the results [MISS]! FN += {fn}", file=eval_log)
            FN += fn

        print_line(c='=', eval_log=eval_log)

    if not IGNORE_FP_TABLES:
        # count remaining FP's
        for key, res_items in res_files.items():
            print(f"'{key}' not found in the reference [FALSE-ALARM]!", file=eval_log)
            fp = 0
            for item in res_items[None]:
                n = len(set(item))
                fp += n

            with open("res_py.csv", "a") as f:
                print(f"{key};{0};{fp};{0}", file=f)
        
            print(f"'{key}' not found in the reference [FALSE-ALARM]! FP += {fp}", file=eval_log)
            FP += fp

    precision, recall, F1, F05 = _calc_scores(TP, FN, FP)

    #print_line(n=100, c='=')
    print(f"FINAL RESULT: {get_scores_str(TP, FN, FP, precision, recall, F1, F05)}", file=eval_log)

    return _get_result(True, TP, FN, FP, precision, recall, F1, F05, eval_log)


def _get_result(status:bool=False, tp:int=0, fn:int=0, fp:int=0, precision:float=0.0, recall:float=0.0, 
        f1:float=0.0, f05:float=0.0, eval_log=None):

    return {
        "status" : status,
        "F1": f1,
        "F0.5": f05,
        "precision": precision,
        "recall": recall,
        "TP": tp,
        "FN": fn,
        "FP": fp,
        "log": eval_log.getvalue() if eval_log != None else ""
    }


if __name__ == "__main__":

    verbose = False
    record_overlap = False
    method = ProcMethod.IAIS
    #method = ProcMethod.Unknown
    
    eval_log = io.StringIO("")
    
    ### REFERENCES
    if method in [ProcMethod.GT, ProcMethod.IAIS, ProcMethod.Tabby, ProcMethod.Abbyy, ProcMethod.Tabula, ProcMethod.Camelot]:
        gt_dir = os.path.join(os.environ['PMC_PATH'], "gt", "scai")
    else:
        gt_dir = os.path.join(os.environ['PMC_PATH'], "dummy", "gt")
        #gt_dir = os.path.join(os.environ['PMC_PATH'], "res", "test2", "gt")
    
    ### RESULTS
    res_multivariant = True

    if method == ProcMethod.GT:
        res_dir = os.path.join(os.environ['PMC_PATH'], "gt", "scai")
        name_pattern = "PMC*-str.xml"
        res_multivariant = False
    elif method == ProcMethod.IAIS:
        res_dir = os.path.join(os.environ['PMC_PATH'], "res", "scai")
        name_pattern = "PMC*-str-result.xml"
    elif method == ProcMethod.Tabby:
        res_dir = os.path.join(os.environ['TABBY_TEST'], "scai", "xml")
        name_pattern = "PMC*-str-result.xml"
    elif method == ProcMethod.Abbyy:
        res_dir = os.path.join(os.environ['PMC_PATH'], "res", "abbyy")
        name_pattern = "PMC*.xml"
    elif method == ProcMethod.Tabula:
        #res_dir = os.path.join(os.environ['PMC_PATH'], "res", "tabula_lattice")
        res_dir = os.path.join(os.environ['PMC_PATH'], "res", "tabula_stream")
        #res_dir = "/data/mnamysl/HBP/tabula-java/results"
        name_pattern = "PMC*-str-result.json"
    elif method == ProcMethod.Camelot:
        #res_dir = os.path.join(os.environ['PMC_PATH'], "res", "camelot_lattice")
        #res_dir = os.path.join(os.environ['PMC_PATH'], "res", "camelot_stream")
        name_pattern = "PMC*.zip"
    else:
        res_dir = os.path.join(os.environ['PMC_PATH'], "dummy", "res")
        name_pattern = "PMC*.xml"
        method = ProcMethod.Abbyy
        #name_pattern = "PMC*-str-result.xml"
        #res_dir = os.path.join(os.environ['PMC_PATH'], "res", "test2", "gt")
        #name_pattern = "PMC*-str.xml"

    tuples_to_use = load_complexity_classes("mavo_table_classes.csv", [0,1,2], eval_log=eval_log) 

    res_files = load_xml_files(res_dir, name_pattern, multivariant=res_multivariant, method=method, 
        tuples_to_use=tuples_to_use, eval_log=eval_log)
    
    print_line(n=100, eval_log=eval_log)    
    
    gt_files = load_xml_files(gt_dir, "PMC*-str.xml", record_overlap=record_overlap, 
        tuples_to_use=tuples_to_use, eval_log=eval_log)

    result = eval_data(gt_files, res_files, res_multivariant=res_multivariant, eval_log=eval_log)

    #print(json.dumps(result, indent=4))
    print(result["log"])
