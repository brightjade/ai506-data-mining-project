import os
import sys
from itertools import combinations
from distutils.util import strtobool

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader

from gensim.models import KeyedVectors
from tqdm import tqdm, trange

from node2vec.node2vec.hypernode2vec import Hypernode2Vec
from hypergraph import HyperGraph
from utils import load_data, embed_nodes, plot_values
from dataset import QueryDataset
from model import SimpleNN


LOAD_PRETRAINED = False
RESULT_DIR = './result'


def find_best_thres(ans_list, pred_list, start, end, intv):
    acc_best = 0
    for thres in np.arange(start, end, intv):
        pred_list_th = np.array(pred_list) > thres
        correct = 0
        for gt, pred in zip(ans_list, list(pred_list_th)):
            if gt == pred:
                correct += 1
        acc = correct / len(ans_list)
        if acc_best < acc:
            acc_best = acc
            thres_best = thres
            intv_s, intv_e = thres, thres + intv

    return acc_best, thres_best, intv_s, intv_e


def evaluate(node_vectors):
    print("Evaluating")
    qp_lines = open('./project_data/query_public.txt', 'r').readlines()
    ap_lines = open('./project_data/answer_public.txt', 'r').readlines()
    qp_iter = iter(qp_lines)
    ap_iter = iter(ap_lines)

    num_queries = int(next(qp_iter))
    num_correct = 0

    sim_lines = []
    ans_lines = []

    for _ in range(num_queries):
        query = next(qp_iter)
        query = query.strip().split()
        ans = next(ap_iter).strip()
        ans = strtobool(ans)
        sim = 0.
        nc = 0
        # sim_threshold = 0.5

        for author1, author2 in combinations(query, 2):
            if author1 in node_vectors.vocab.keys() and author2 in node_vectors.vocab.keys():  # check for fake authors
                sim += node_vectors.similarity(author1, author2)  # compute cosine similarity between two nodes
            nc += 1
        sim = sim / nc
        ans_lines.append(ans)
        sim_lines.append(sim)

    acc_best, thres_best, s, e = find_best_thres(ans_lines, sim_lines, 0., 1., 0.005)
    print(acc_best, thres_best)

    return acc_best, thres_best


def visualize(node_vectors, p1, p2, p, q):
    # Visualize
    print("Visualize")
    tsne = TSNE(n_components=2).fit_transform(node_vectors.vectors)
    plt.figure()
    plt.scatter(tsne[:,0], tsne[:,1])
    plt.savefig(os.path.join(RESULT_DIR, f"hypernode2vec_p({p})q({q})_p1({p1})p2({p2}).png"))
    plt.close()


def genHypernode2vec(HG, p1, p2, p, q, is_load=None):

    if is_load is None:
        hypernode2vec = Hypernode2Vec(graph=HG,
                                      dimensions=64,
                                      walk_length=10,
                                      num_walks=100,
                                      p1=p1,
                                      p2=p2,
                                      p=p, q=q,
                                      workers=8)
        model = hypernode2vec.fit(window=10, min_count=1)
        node_vectors = model.wv
        model.save(os.path.join(RESULT_DIR, f"hypernode2vec_p({p})q({q})_p1({p1})p2({p2}).model"))  # save model in case of more training later
        model.wv.save(os.path.join(RESULT_DIR, f"hypernode2vec_p({p})q({q})_p1({p1})p2({p2}).kv"))  # keyed vectors for later use save memory by not loading entire model
        del model  # save memory during computation

    elif is_load is not None and os.path.isfile(is_load):
        print("Load saved keyed vectors")
        node_vectors = KeyedVectors.load("hypernode2vec.kv")

    elif is_load is not None and not os.path.isfile(is_load):
        raise KeyError('node vector file does not exist')

    return node_vectors


def train(model, train_set, val_set, criterion, optimizer, max_epochs):
    train_losses, val_losses = [], []
    train_accuracies, val_accuracies = [], []

    train_loader = DataLoader(train_set, batch_size=32, num_workers=2)
    val_loader = DataLoader(val_set, batch_size=32, num_workers=2)

    loss_log = tqdm(total=0, bar_format='{desc}', position=3)
    acc_log = tqdm(total=0, bar_format='{desc}', position=4)

    for epoch in trange(max_epochs, desc="Epoch", position=2):
        train_loss, train_acc = [], []
        model.train()
        for queries, labels in tqdm(train_loader, desc="Training Iteration", position=1):
            optimizer.zero_grad()
            scores = model(queries)
            loss = criterion(scores, labels)
            loss.backward()
            optimizer.step()
            
            _, pred = torch.max(scores.data, dim=1)
            acc = (pred == labels).sum().item() / len(labels)

            train_loss.append(loss.item())
            train_acc.append(acc)
            
            des1 = 'Training Loss: {:06.4f}'.format(loss.cpu())
            des2 = 'Training Acc: {:.0%}'.format(acc)
            loss_log.set_description_str(des1)
            acc_log.set_description_str(des2)
            del loss
    
        train_losses.append(sum(train_loss) / len(train_loss))
        train_accuracies.append(sum(train_acc) / len(train_acc))

        val_loss, val_acc = [], []
        model.eval()
        with torch.no_grad():
            for queries, labels in tqdm(val_loader, desc="Validation Iteration", position=1):
                scores = model(queries)
                loss = criterion(scores, labels)

                _, pred = torch.max(scores.data, dim=1)
                acc = (pred == labels).sum().item() / len(labels)

                val_loss.append(loss.item())
                val_acc.append(acc)

                des1 = 'Validation Loss: {:06.4f}'.format(loss.cpu())
                des2 = 'Validation Acc: {:.0%}'.format(acc)
                loss_log.set_description_str(des1)
                acc_log.set_description_str(des2)
                del loss
        
        val_losses.append(sum(val_loss) / len(val_loss))
        val_accuracies.append(sum(val_acc) / len(val_acc))

    return train_losses, val_losses, train_accuracies, val_accuracies 


def predict(model, test_set):
    pass


if __name__ == "__main__":
    if not os.path.isdir(RESULT_DIR):
        os.mkdir(RESULT_DIR)

    graph_data = './project_data/paper_author.txt'
    query_data = './project_data/query_public.txt'
    label_data = './project_data/answer_public.txt'
    test_data = './project_data/query_private.txt'

    # TODO: check if we want to evaluate hypernode2vec or node2vec
    #
    #

    # Load pretrained vectors, otherwise create a new graph
    if os.path.exists("node2vec.kv"):
        node_vectors = KeyedVectors.load("node2vec.kv", mmap='r')
    else:
        node_vectors = embed_nodes(graph_data)

    # hypernode2vec creation
    # HG = HyperGraph()
    # for _ in range(num_pubs):
    #     linei = next(lineiter)
    #     authors = list(map(int, linei.strip().split()))

    #     edge_list = [e for e in combinations(authors, 2)]
    #     HG.add_edges_from(edge_list)
    #     HG.update_hyperedges(authors)

    # pq = [(1,1), (1,0.5), (1,2)] # [DeepWalk, reflecting homophily, reflecting structural equivalence]
    # p1p2 = [(1,1), (1,0.5), (1,2)]

    # result = []
    # num_run = int(len(pq) * len(p1p2))
    # current = 0
    # for p, q in pq:
    #     for p1, p2 in p1p2:
    #         current += 1
    #         print(f"p: {p}, q: {q}, p1: {p1}, p2: {p2} start ({current}/{num_run})")

    #         node_vectors = genHypernode2vec(HG, p1=p1, p2=p2, p=p, q=q)

    #         # visualize(node_vectors, p1=p1, p2=p2, p=p, q=q)
    #         acc_best, thres_best = evaluate(node_vectors)
    #         result.append({'p':p, 'q':q, 'p1':p1, 'p2':p2, 'thres_best':thres_best, 'acc_best':acc_best})

    # print('Saving final result')
    # pddf = pd.DataFrame(result)
    # pddf.to_csv(os.path.join(RESULT_DIR, "result_%s.csv" % time.strftime("%Y%m%d_%H:%M:%S")))

    # Split and Shuffle Data
    query_train, query_val, label_train, label_val = load_data(query_data, label_data, node_vectors)
    train_set = QueryDataset(query_train, label_train)
    val_set = QueryDataset(query_val, label_val)

    # Load pretrained model, otherwise train parameters
    model = SimpleNN()
    pretrained_model_path = "node2vec_fronorm.pth"
    if os.path.exists(pretrained_model_path):
        model.load_state_dict(torch.load(pretrained_model_path), strict=False)
    else:
        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters(), lr=1e-3)
        train_losses, val_losses, train_accuracies, val_accuracies = train(model, train_set, val_set, criterion, optimizer, max_epochs=200)
        torch.save(model.state_dict(), pretrained_model_path)

        print("Final training loss: {:06.4f}".format(train_losses[-1]))
        print("Final validation loss: {:06.4f}".format(val_losses[-1]))
        print("Final training accuracy: {:06.4f}".format(train_accuracies[-1]))
        print("Final validation accuracy: {:06.4f}".format(val_accuracies[-1]))

        plot_values(train_losses, val_losses, title="Losses")
        plot_values(train_accuracies, val_accuracies, title="Accuracies")

    # TODO: Predict test data  ## save to answer_private.txt
    # predict(test_data)
