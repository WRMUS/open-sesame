# -*- coding: utf-8 -*-
import os
import sys
import time
from optparse import OptionParser

from arksemaforeval import *
from dynet import *
from evaluation import *
from raw_data import make_data_instance


optpr = OptionParser()
optpr.add_option("--mode", dest="mode", type="choice", choices=["train", "test", "refresh", "predict"], default="train")
optpr.add_option("-n", "--model_name", help="Name of model directory to save model to.")
optpr.add_option("--nodrop", action="store_true", default=False)
optpr.add_option("--nowordvec", action="store_true", default=False)
optpr.add_option("--hier", action="store_true", default=False)
optpr.add_option("--exemplar", action="store_true", default=False)
optpr.add_option("--raw_input", type="str", metavar="FILE")
(options, args) = optpr.parse_args()

model_dir = "logs/{}/".format(options.model_name)
model_file_name = "{}best-frameid-{}-model".format(model_dir, VERSION)
if not os.path.exists(model_dir):
    os.makedirs(model_dir)

if options.exemplar:
    train_conll = TRAIN_EXEMPLAR
else:
    train_conll = TRAIN_FTE

USE_DROPOUT = not options.nodrop
if options.mode in ["test", "predict"]:
    USE_DROPOUT = False
USE_WV = not options.nowordvec
USE_HIER = options.hier

sys.stderr.write("\nCOMMAND: " + " ".join(sys.argv) + "\n")
sys.stderr.write("\nPARSER SETTINGS\n_____________________\n")
sys.stderr.write("PARSING MODE:   \t" + options.mode + "\n")
sys.stderr.write("USING EXEMPLAR? \t" + str(options.exemplar) + "\n")
sys.stderr.write("USING DROPOUT?  \t" + str(USE_DROPOUT) + "\n")
sys.stderr.write("USING WORDVECS? \t" + str(USE_WV) + "\n")
sys.stderr.write("USING HIERARCHY?\t" + str(USE_HIER) + "\n")
if options.mode in ["train", "refresh"]:
    sys.stderr.write("VALIDATED MODEL WILL BE SAVED TO\t{}\n".format(model_file_name))
else:
    sys.stderr.write("MODEL USED FOR TEST / PREDICTION:\t{}\n".format(model_file_name))
sys.stderr.write("_____________________\n")

UNK_PROB = 0.1
DROPOUT_RATE = 0.01

TOKDIM = 60
POSDIM = 4
LUDIM = 64
LPDIM = 5
INPDIM = TOKDIM + POSDIM

LSTMINPDIM = 64
LSTMDIM = 64
LSTMDEPTH = 2
HIDDENDIM = 64


def find_multitokentargets(examples, split):
    multitoktargs = tottargs = 0.0
    for tr in examples:
        tottargs += 1
        if len(tr.targetframedict) > 1:
            multitoktargs += 1
            tfs = set(tr.targetframedict.values())
            if len(tfs) > 1:
                raise Exception("different frames for neighboring targets!", tr.targetframedict)
    sys.stderr.write("multi-token targets in %s: %.3f%% [%d / %d]\n"
                     %(split, multitoktargs*100/tottargs, multitoktargs, tottargs))

trainexamples, m, t = read_conll(train_conll)
find_multitokentargets(trainexamples, "train")

post_train_lock_dicts()
lufrmmap, relatedlus = read_related_lus()
if USE_WV:
    wvs = get_wvec_map()
    PRETDIM = len(wvs.values()[0])
    sys.stderr.write("using pretrained embeddings of dimension " + str(PRETDIM) + "\n")


lock_dicts()
UNKTOKEN = VOCDICT.getid(UNK)

sys.stderr.write("# words in vocab: " + str(VOCDICT.size()) + "\n")
sys.stderr.write("# POS tags: " + str(POSDICT.size()) + "\n")
sys.stderr.write("# lexical units: " + str(LUDICT.size()) + "\n")
sys.stderr.write("# LU POS tags: " + str(LUPOSDICT.size()) + "\n")
sys.stderr.write("# frames: " + str(FRAMEDICT.size()) + "\n")

if options.mode in ["train", "refresh"]:
    devexamples, m, t = read_conll(DEV_CONLL)
    find_multitokentargets(devexamples, "dev/test")
    sys.stderr.write("unknowns in dev\n\n_____________________\n")
    out_conll_file = "{}predicted-{}-frameid-dev.conll".format(model_dir, VERSION)
elif options.mode  == "test":
    devexamples, m, t = read_conll(TEST_CONLL)
    find_multitokentargets(devexamples, "dev/test")
    sys.stderr.write("unknowns in test\n\n_____________________\n")
    out_conll_file = "{}predicted-{}-frameid-test.conll".format(model_dir, VERSION)
    fefile = "{}predicted-{}-frameid-test.fes".format(model_dir, VERSION)
elif options.mode == "predict":
    assert options.raw_input is not None
    instances, _, _ = read_conll(options.raw_input)
    out_conll_file = "{}predicted-frames.conll".format(model_dir)
else:
    raise Exception("Invalid parser mode", options.mode)


sys.stderr.write("# unseen, unlearnt test words in vocab: " + str(VOCDICT.num_unks()) + "\n")
sys.stderr.write("# unseen, unlearnt test POS tags: " + str(POSDICT.num_unks()) + "\n")
sys.stderr.write("# unseen, unlearnt test lexical units: " + str(LUDICT.num_unks()) + "\n")
sys.stderr.write("# unseen, unlearnt test LU pos tags: " + str(LUPOSDICT.num_unks()) + "\n")
sys.stderr.write("# unseen, unlearnt test frames: " + str(FRAMEDICT.num_unks()) + "\n\n")

model = Model()
trainer = SimpleSGDTrainer(model)
# trainer = AdamTrainer(model, 0.0001, 0.01, 0.9999, 1e-8)

v_x = model.add_lookup_parameters((VOCDICT.size(), TOKDIM))
p_x = model.add_lookup_parameters((POSDICT.size(), POSDIM))
lu_x = model.add_lookup_parameters((LUDICT.size(), LUDIM))
lp_x = model.add_lookup_parameters((LUPOSDICT.size(), LPDIM))
if USE_WV:
    e_x = model.add_lookup_parameters((VOCDICT.size(), PRETDIM))
    for wordid in wvs:
        e_x.init_row(wordid, wvs[wordid])
    w_e = model.add_parameters((LSTMINPDIM, PRETDIM))
    b_e = model.add_parameters((LSTMINPDIM, 1))

w_i = model.add_parameters((LSTMINPDIM, INPDIM))
b_i = model.add_parameters((LSTMINPDIM, 1))

builders = [
    LSTMBuilder(LSTMDEPTH, LSTMINPDIM, LSTMDIM, model),
    LSTMBuilder(LSTMDEPTH, LSTMINPDIM, LSTMDIM, model),
]

tlstm = LSTMBuilder(LSTMDEPTH, 2*LSTMDIM, LSTMDIM, model)

w_z = model.add_parameters((HIDDENDIM, LSTMDIM + LUDIM + LPDIM))
b_z = model.add_parameters((HIDDENDIM, 1))
w_f = model.add_parameters((FRAMEDICT.size(), HIDDENDIM))
b_f = model.add_parameters((FRAMEDICT.size(), 1))

def identify_frames(builders, tokens, postags, lexunit, targetpositions, goldframe=None):
    renew_cg()
    trainmode = (goldframe is not None)

    sentlen = len(tokens) - 1
    emb_x = [v_x[tok] for tok in tokens]
    pos_x = [p_x[pos] for pos in postags]

    pw_i = parameter(w_i)
    pb_i = parameter(b_i)

    emb2_xi = [(pw_i * concatenate([emb_x[i], pos_x[i]])  + pb_i) for i in xrange(sentlen+1)]
    if USE_WV:
        pw_e = parameter(w_e)
        pb_e = parameter(b_e)
        for i in xrange(sentlen+1):
            if tokens[i] in wvs:
                nonupdatedwv = e_x[tokens[i]]  # prevent the wvecs from being updated
                emb2_xi[i] = emb2_xi[i] + pw_e * nonupdatedwv + pb_e

    emb2_x = [rectify(emb2_xi[i]) for i in xrange(sentlen+1)]

    pw_z = parameter(w_z)
    pb_z = parameter(b_z)
    pw_f = parameter(w_f)
    pb_f = parameter(b_f)

    # initializing the two LSTMs
    if USE_DROPOUT and trainmode:
        builders[0].set_dropout(DROPOUT_RATE)
        builders[1].set_dropout(DROPOUT_RATE)
    f_init, b_init = [i.initial_state() for i in builders]

    fw_x = f_init.transduce(emb2_x)
    bw_x = b_init.transduce(reversed(emb2_x))

    # only using the first target position - summing them hurts :(
    targetembs = [concatenate([fw_x[targetidx], bw_x[sentlen - targetidx - 1]]) for targetidx in targetpositions]
    targinit = tlstm.initial_state()
    target_vec = targinit.transduce(targetembs)[-1]

    valid_frames = list(lufrmmap[lexunit.id])
    chosenframe = valid_frames[0]
    logloss = None
    if len(valid_frames) > 1:
        if USE_HIER and lexunit.id in relatedlus:
            lu_vec = esum([lu_x[luid] for luid in relatedlus[lexunit.id]])
        else:
            lu_vec = lu_x[lexunit.id]
        fbemb_i = concatenate([target_vec, lu_vec, lp_x[lexunit.posid]])
        # TODO(swabha): Add more Baidu-style features here.
        f_i = pw_f * rectify(pw_z * fbemb_i + pb_z) + pb_f
        if trainmode and USE_DROPOUT:
            f_i = dropout(f_i, DROPOUT_RATE)

        logloss = log_softmax(f_i, valid_frames)

        if not trainmode:
            chosenframe = np.argmax(logloss.npvalue())

    if trainmode: chosenframe = goldframe.id

    losses = []
    if logloss is not None:
        losses.append(pick(logloss, chosenframe))

    prediction = {tidx: (lexunit, Frame(chosenframe)) for tidx in targetpositions}

    objective = -esum(losses) if losses else None
    return objective, prediction

def print_as_conll(goldexamples, pred_targmaps):
    with codecs.open(out_conll_file, "w", "utf-8") as f:
        for g,p in zip(goldexamples, pred_targmaps):
            result = g.get_predicted_frame_conll(p) + "\n"
            f.write(result)
        f.close()


# main
NUMEPOCHS = 10
if options.exemplar:
    NUMEPOCHS = 25
EVAL_EVERY_EPOCH = 100
DEV_EVAL_EPOCH = 5 * EVAL_EVERY_EPOCH

best_dev_f1 = 0.0
if options.mode in ["refresh"]:
    sys.stderr.write("Reusing model from {} ...\n".format(model_file_name))
    model.populate(model_file_name)
    with open(os.path.join(model_dir, "best-dev-f1.txt"), "r") as fin:
        for line in fin:
            best_dev_f1 = float(line.strip())
    fin.close()
    sys.stderr.write("Best dev F1 so far = %.4f\n" % best_dev_f1)

if options.mode in ["train", "refresh"]:
    tagged = loss = 0.0

    for epoch in xrange(NUMEPOCHS):
        random.shuffle(trainexamples)
        for idx, trex in enumerate(trainexamples, 1):
            if idx % EVAL_EVERY_EPOCH == 0:
                trainer.status()
                sys.stderr.write("%d loss = %.6f\n" %(idx, loss/tagged))
                tagged = loss = 0.0
            inptoks = []
            unk_replace_tokens(trex.tokens, inptoks, VOCDICT, UNK_PROB, UNKTOKEN)

            trexloss,_ = identify_frames(
                builders, inptoks, trex.postags, trex.lu, trex.targetframedict.keys(), trex.frame)

            if trexloss is not None:
                loss += trexloss.scalar_value()
                trexloss.backward()
                trainer.update()
            tagged += 1

            if idx % DEV_EVAL_EPOCH == 0:
                corpus_result = [0.0, 0.0, 0.0]
                devtagged = devloss = 0.0
                predictions = []
                for devex in devexamples:
                    devludict = devex.get_only_targets()
                    dl, predicted = identify_frames(
                        builders, devex.tokens, devex.postags, devex.lu, devex.targetframedict.keys())
                    if dl is not None:
                        devloss += dl.scalar_value()
                    predictions.append(predicted)

                    devex_result = evaluate_example_frameid(devex.frame, predicted)
                    corpus_result = np.add(corpus_result, devex_result)
                    devtagged += 1

                devp, devr, devf = calc_f(corpus_result)
                devtp, devfp, devfn = corpus_result
                sys.stderr.write("[dev epoch=%d] loss = %.6f "
                                 "p = %.4f (%.1f/%.1f) r = %.4f (%.1f/%.1f) f1 = %.4f"
                                 % (epoch, devloss/devtagged,
                                    devp, devtp, devtp + devfp,
                                    devr, devtp, devtp + devfn,
                                    devf))
                if devf > best_dev_f1:
                    best_dev_f1 = devf
                    with open(os.path.join(model_dir, "best-dev-f1.txt"), "w") as fout:
                        fout.write("{}\n".format(best_dev_f1))
    
                    print_as_conll(devexamples, predictions)
                    sys.stderr.write(" -- saving to {}".format(model_file_name))
                    model.save(model_file_name)
                sys.stderr.write("\n")

elif options.mode == "test":
    model.populate(model_file_name)
    corpus_tpfpfn = [0.0, 0.0, 0.0]

    testpredictions = []

    sn = devexamples[0].sent_num
    sl = [0.0,0.0,0.0]
    logger = open("{}/frameid-prediction-analysis.log".format(model_dir), "w")
    logger.write("Sent#%d :\n" % sn)
    devexamples[0].print_internal_sent(logger)

    for testex in devexamples:
        _, predicted = identify_frames(builders, testex.tokens, testex.postags, testex.lu, testex.targetframedict.keys())

        tpfpfn = evaluate_example_frameid(testex.frame, predicted)
        corpus_tpfpfn = np.add(corpus_tpfpfn, tpfpfn)

        testpredictions.append(predicted)

        sentnum = testex.sent_num
        if sentnum != sn:
            lp, lr, lf = calc_f(sl)
            logger.write("\t\t\t\t\t\t\t\t\tTotal: %.1f / %.1f / %.1f\n"
                  "Sentence ID=%d: Recall=%.5f (%.1f/%.1f) Precision=%.5f (%.1f/%.1f) Fscore=%.5f"
                  "\n-----------------------------\n"
                  % (sl[0], sl[0]+sl[1], sl[0]+sl[-1],
                     sn,
                     lr, sl[0], sl[-1] + sl[0],
                     lp, sl[0], sl[1] + sl[0],
                     lf))
            sl = [0.0,0.0,0.0]
            sn = sentnum
            logger.write("Sent#%d :\n" % sentnum)
            testex.print_internal_sent(logger)

        logger.write("gold:\n")
        testex.print_internal_frame(logger)
        logger.write("prediction:\n")
        testex.print_external_frame(predicted, logger)

        sl = np.add(sl, tpfpfn)
        logger.write("{} / {} / {}\n".format(tpfpfn[0], tpfpfn[0]+tpfpfn[1], tpfpfn[0]+tpfpfn[-1]))

    # last sentence
    lp, lr, lf = calc_f(sl)
    logger.write("\t\t\t\t\t\t\t\t\tTotal: %.1f / %.1f / %.1f\n"
          "Sentence ID=%d: Recall=%.5f (%.1f/%.1f) Precision=%.5f (%.1f/%.1f) Fscore=%.5f"
          "\n-----------------------------\n"
          % (sl[0], sl[0]+sl[1], sl[0]+sl[-1],
             sentnum,
             lp, sl[0], sl[1] + sl[0],
             lr, sl[0], sl[-1] + sl[0],
             lf))

    testp, testr, testf = calc_f(corpus_tpfpfn)
    testtp, testfp, testfn = corpus_tpfpfn
    sys.stderr.write("[test] p = %.4f (%.1f/%.1f) r = %.4f (%.1f/%.1f) f1 = %.4f\n" %(
        testp, testtp, testtp + testfp,
        testr, testtp, testtp + testfp,
        testf))

    sys.stderr.write("Printing output conll to " + out_conll_file + " ... ")
    print_as_conll(devexamples, testpredictions)
    sys.stderr.write("Done!\n")

    sys.stderr.write("Printing frame-elements to " + fefile + " ...\n")
    convert_conll_to_frame_elements(out_conll_file, fefile)
    sys.stderr.write("Done!\n")

elif options.mode == "predict":
    model.populate(model_file_name)

    predictions = []
    for instance in instances:
        _, prediction = identify_frames(builders, instance.tokens, instance.postags, instance.lu, instance.targetframedict.keys())
        predictions.append(prediction)
    sys.stderr.write("Printing output in CoNLL format to {}\n".format(out_conll_file))
    print_as_conll(instances, predictions)
    sys.stderr.write("Done!\n")

logger.close()