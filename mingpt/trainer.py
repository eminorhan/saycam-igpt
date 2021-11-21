"""
Simple training loop; Boilerplate that could apply to any arbitrary neural network,
so nothing in this file really has anything to do with GPT specifically.
"""

import math
import numpy as np
import torch
from torch.utils.data.dataloader import DataLoader


class TrainerConfig:
    # optimization parameters
    max_epochs = 10
    batch_size = 64

    # checkpoint settings
    ckpt_path = None
    num_workers = 0  # for DataLoader

    def __init__(self, **kwargs):
        for k,v in kwargs.items():
            setattr(self, k, v)

class Trainer:

    def __init__(self, model, optimizer, train_dataset, test_dataset, config):
        self.model = model
        self.optimizer = optimizer
        self.train_dataset = train_dataset
        self.test_dataset = test_dataset
        self.config = config
        self.train_loss = None
        self.test_loss = None

    def save_checkpoint(self):
        # DataParallel wrappers keep raw model object in .module attribute
        raw_model = self.model.module if hasattr(self.model, "module") else self.model
        optimizer = self.optimizer
        print('Saving to:', self.config.ckpt_path)
        torch.save({'model_state_dict': raw_model.state_dict(), 
                    'optimizer_state_dict': optimizer.state_dict(),
                    'train_loss': self.train_loss,
                    'test_loss': self.test_loss
                    }, self.config.ckpt_path)

    def train(self, args):
        model, optimizer, config = self.model, self.optimizer, self.config

        def run_epoch(split, epoch):
            is_train = split == 'train'
            model.train(is_train)
            data = self.train_dataset if is_train else self.test_dataset

            if args.distributed:
                train_sampler = torch.utils.data.distributed.DistributedSampler(data)
                loader = DataLoader(data, shuffle=False, pin_memory=True, sampler=train_sampler, batch_size=config.batch_size, num_workers=config.num_workers)
            else:
                loader = DataLoader(data, shuffle=True, pin_memory=True, batch_size=config.batch_size, num_workers=config.num_workers)

            if args.distributed: 
                loader.sampler.set_epoch(epoch)

            losses = []
            print_freq = max(1, len(loader) // 5)  # print results 5 times every epoch

            for it, (x, y) in enumerate(loader):
                # place data on the correct device
                x = x.cuda(non_blocking=True)
                y = y.cuda(non_blocking=True)

                # forward the model
                with torch.set_grad_enabled(is_train):
                    _, loss = model(x, y)  # the first output returns the logits, which we don't need for now
                    loss = loss.mean()  # collapse all losses if they are scattered on multiple gpus
                    losses.append(loss.item())

                if is_train:
                    # backprop and update the parameters
                    model.zero_grad()
                    loss.backward()
                    optimizer.step()

                # report progress
                if it % print_freq == 0:
                    print('Epoch:', epoch, '|', 'Iteration:', it, 'of', len(loader), '|', 'Loss (up to this point in this epoch):', float(np.mean(losses)))

            epoch_loss = float(np.mean(losses))
            return epoch_loss

        for epoch in range(config.max_epochs):
            self.train_loss = run_epoch('train', epoch)

        # test once at the end of training
        if self.test_dataset is not None:
            self.test_loss = run_epoch('test')

        # save model
        if args.rank == 0:
            self.save_checkpoint()