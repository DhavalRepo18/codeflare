import ray

from com.ibm.research.ray.graph.Datamodel import Node
from com.ibm.research.ray.graph.Datamodel import Edge
from com.ibm.research.ray.graph.Datamodel import Pipeline
from com.ibm.research.ray.graph.Datamodel import XY
from com.ibm.research.ray.graph.Datamodel import XYRef

import sklearn.base as base
from enum import Enum


class ExecutionType(Enum):
    TRAIN = 0,
    TEST = 1


@ray.remote
def execute_or_node_inner(node: Node, train_mode: ExecutionType, Xy: XYRef):
    estimator = node.get_estimator()
    # Blocking operation -- not avoidable
    # Xy = ray.get(Xy_ref)

    X = ray.get(Xy.get_Xref())
    y = ray.get(Xy.get_yref())
    print('X shape: ' + str(X.shape))
    print('Y shape: ' + str(y.shape))

    if train_mode == ExecutionType.TRAIN:
        if base.is_classifier(estimator) or base.is_regressor(estimator):
            # Always clone before fit, else fit is invalid
            cloned_estimator = base.clone(estimator)
            cloned_estimator.fit(X, y)
            # TODO: For now, make yref passthrough - this has to be fixed more comprehensively
            res_Xref = ray.put(cloned_estimator.predict(X))
            result = [XYRef(res_Xref, Xy.get_yref())]
            return result
        else:
            # No need to clone as it is a transform pass through on the fitted estimator
            res_Xref = ray.put(estimator.fit_transform(X))
            result = [XYRef(res_Xref, Xy.get_yref())]
            return result
    elif train_mode == ExecutionType.TEST:
        # Test mode does not clone as it is a simple predict or transform
        if base.is_classifier(estimator) or base.is_regressor(estimator):
            res_Xref = estimator.predict(X)
            result = [XYRef(res_Xref, Xy.get_yref())]
            return result
        else:
            res_Xref = estimator.transform(X)
            result = [XY(res_Xref, Xy.get_yref())]
            return result


###
# in_args is a dict from Node to list of XYRefs
###
def execute_pipeline(pipeline: Pipeline, mode: ExecutionType, in_args: dict):
    nodes_by_level = pipeline.get_nodes_by_level()

    # track args per edge
    edge_args = {}
    for node, node_in_args in in_args.items():
        pre_edges = pipeline.get_pre_edges(node)
        for pre_edge in pre_edges:
            edge_args[pre_edge] = node_in_args

    for nodes in nodes_by_level:
        for node in nodes:
            pre_edges = pipeline.get_pre_edges(node)
            post_edges = pipeline.get_post_edges(node)
            if not node.get_and_flag():
                execute_or_node(node, pre_edges, edge_args, post_edges, mode)
            else:
                cross_product = execute_and_node(node, pre_edges, edge_args, post_edges)
                for element in cross_product:
                    print(element)

    out_args = {}
    last_level_nodes = nodes_by_level[pipeline.compute_max_level()]
    for last_level_node in last_level_nodes:
        edge = Edge(last_level_node, None)
        out_args[last_level_node] = edge_args[edge]

    return out_args


# def execute_and_node_inner(node: Node, mode: ExecutionType, element):


# def execute_and_node(node, pre_edges, edge_args, post_edges, mode: ExecutionType):
#     edge_args_lists = list()
#     for pre_edge in pre_edges:
#         edge_args_lists.append(edge_args[pre_edge])
#
#     # cross product using itertools
#     import itertools
#     cross_product = itertools.product(*edge_args_lists)
#
#     for element in cross_product:
#         exec_xyrefs = execute_and_node_inner(node, mode, element)
#         for post_edge in post_edges:
#             if post_edge not in edge_args.keys():
#                 edge_args[post_edge] = []
#             edge_args[post_edge].extend(exec_xyrefs)


def execute_or_node(node, pre_edges, edge_args, post_edges, mode: ExecutionType):
    for pre_edge in pre_edges:
        Xyrefs = edge_args[pre_edge]
        exec_xyrefs = []
        for xy_ref in Xyrefs:
            xy_ref_list = ray.get(xy_ref)
            for xy_ref in xy_ref_list:
                inner_result = execute_or_node_inner.remote(node, mode, xy_ref)
                exec_xyrefs.append(inner_result)

        for post_edge in post_edges:
            if post_edge not in edge_args.keys():
                edge_args[post_edge] = []
            edge_args[post_edge].extend(exec_xyrefs)
