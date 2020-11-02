from .model import TransformerHtr
from .data import HtrDataset, Batch
import os, time
from torch.nn import Module, KLDivLoss
from torch.utils.data import DataLoader
from torch.optim import Adam
from torch import nonzero, no_grad, save
from .data import HtrDataset
import numpy as np
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

class NoamOpt:
    "Optim wrapper that implements rate."
    def __init__(self, model_size, factor, warmup, optimizer):
        self.optimizer = optimizer
        self._step = 0
        self.warmup = warmup
        self.factor = factor
        self.model_size = model_size
        self._rate = 0
        
    def step(self):
        "Update parameters and rate"
        self._step += 1
        rate = self.rate()
        for p in self.optimizer.param_groups:
            p['lr'] = rate
        self._rate = rate
        self.optimizer.step()
        
    def rate(self, step = None):
        "Implement `lrate` above"
        if step is None:
            step = self._step
        return self.factor * \
            (self.model_size ** (-0.5) *
            min(step ** (-0.5), step * self.warmup ** (-1.5)))
        


class LabelSmoothing(Module):
    "Implement label smoothing."
    def __init__(self, size, padding_idx=0, smoothing=0.0):
        super(LabelSmoothing, self).__init__()
        self.criterion = KLDivLoss(size_average=False)
        self.padding_idx = padding_idx
        self.confidence = 1.0 - smoothing
        self.smoothing = smoothing
        self.size = size
        self.true_dist = None
        
    def forward(self, x, target):
        assert x.size(1) == self.size
        true_dist = x.data.clone()
        true_dist.fill_(self.smoothing / (self.size - 2))
        true_dist.scatter_(1, target.data.unsqueeze(1), self.confidence)
        true_dist[:, self.padding_idx] = 0
        mask = torch.nonzero(target.data == self.padding_idx)
        if mask.dim() > 0:
            true_dist.index_fill_(0, mask.squeeze(), 0.0)
        self.true_dist = true_dist
        return self.criterion(x, Variable(true_dist, requires_grad=False))

class SimpleLossCompute:
    "A simple loss compute and train function."
    def __init__(self, generator, criterion, opt=None):
        self.generator = generator
        self.criterion = criterion
        self.opt = opt
        
    def __call__(self, x, y, norm):
        x = self.generator(x)
        loss = self.criterion(x.contiguous().view(-1, x.size(-1)), 
                              y.contiguous().view(-1)) / norm
        #loss.backward()
        if self.opt is not None:
            loss.backward()
            self.opt.step()
            self.opt.optimizer.zero_grad()
        return loss.data * norm

def run_epoch(dataloader, model, loss_compute):
    "Standard Training and Logging Function"
    start = time.time()
    total_tokens = 0
    total_loss = 0
    tokens = 0
    for i, (imgs,  labels_y, labels) in enumerate(dataloader):
        batch = Batch(imgs, labels, labels_y)
        out = model(batch.src, batch.trg, batch.src_mask, batch.trg_mask)
        loss = loss_compute(out, batch.trg_y, batch.ntokens)
        total_loss += loss
        total_tokens += batch.ntokens
        tokens += batch.ntokens
        if i % 50 == 1:
            elapsed = time.time() - start
            print("Epoch Step: %d Loss: %f Tokens per Sec: %f" %
                    (i, loss / batch.ntokens, tokens / elapsed))
            start = time.time()
            tokens = 0
    return total_loss / total_tokens


def train(batch_size = 25, cuda=False):
    train_dataloader = DataLoader(HtrDataset(), batch_size=batch_size, shuffle=True, num_workers=0)
    val_dataloader = DataLoader(HtrDataset(pt='valid'), batch_size=batch_size, shuffle=False, num_workers=0)
    model = TransformerHtr(97)
    criterion = LabelSmoothing(size=97, padding_idx=0, smoothing=0.1)
    if cuda:
        model.cuda()
        criterion.cuda()
    model_opt = NoamOpt(model.tgt_embed[0].d_model, 1, 2000,
            Adam(model.parameters(), lr=0, betas=(0.9, 0.98), eps=1e-9))
    for epoch in range(1):
        model.train()
        run_epoch(train_dataloader, model, 
              SimpleLossCompute(model.generator, criterion, model_opt))
        model.eval()
        with torch.no_grad():
            test_loss = run_epoch(val_dataloader, model, 
                  SimpleLossCompute(model.generator, criterion, None))
            print("test_loss", test_loss)
        torch.save(model.state_dict(), '%08d_%f.pth'%(epoch, test_loss))
    return model
