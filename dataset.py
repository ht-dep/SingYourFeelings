#!/usr/bin/env python

import random
import yaml

from midiutil import MIDIFile
import pandas as pd
import torch
import word2vec as w2v

import config
import lyrics2vec as l2v
import midi2vec as m2v

lex = w2v.load(config.lyrics.lex)
vs = len(lex.vocab)

def vec2midi(vec):
  '''
  vec: (note, tempo)
    note: list, Ci x L
    tempo: float
  '''
  notes, tempo = vec

  midi = MIDIFile(config.music.Ci)
  midi.addTempo(0, 0, tempo*config.tempo.default)

  feat2id = config.music.feat2id.items()
  for channel, channel_notes in enumerate(notes):
    time = 0
    for note_emb in channel_notes:
      note = m2v.id2Note(note_emb)
      note = {feat: note[id] for feat, id in feat2id}
      note['time'] = time = note['time'] + time
      midi.addNote(track=channel, channel=channel, **note)
  return midi

class Dataset:
  def __init__(self, inp, tar, batch_size, pad_value=0):
    '''
    inp, tar: dict of (lyrics, note, tempo)
      lyrics: list, lyrics vector
      note: list, note matrix of the music snippet
      tempo: int, tempo of the music snippet
    '''
    self.bs = batch_size
    self.inp = inp
    self.tar = tar

    for data in [self.inp, self.tar]:
      if 'lyrics' in data:
        lyrics = self.padLyrics(data, pad_value)
        data['lyrics'] = lyrics
        self.size = len(data['lyrics'])
      if 'note' in data:
        note = self.padNote(data)
        data['note'] = note
        self.size = len(data['note'])

  def padNote(self, data):
    def padNote(note):
      Ci, E, L = config.music.Ci, config.music.E, config.music.L
      if len(note) > Ci: note = note[:Ci]
      for track in note:
        if len(track) < L:
          yield track + [ 0 for _ in range(L-len(track)) ]
        else:
          yield track[:L]

      for _ in range(len(note), Ci):
        yield [0 for _ in range(L) ]

    return [ list(padNote(note)) for note in data['note'] ]

  def padLyrics(self, data, pv):
    def padLyrics(lyr):
      L = config.lyrics.L
      if len(lyr) < L:
        return lyr + [pv]*( L-len(lyr) )
      else:
        return lyr[:L]

    return [ padLyrics(lyrics) for lyrics in data['lyrics'] ]

  def __getitem__(self, i):
    if i >= len(self):
      raise IndexError('Index %d out of range (%d)' % (i, len(self)-1))
    begin = self.bs * i
    end = begin + self.bs
    if end >= self.size:
      end = None

    pair = [None, None]    # [inp, tar]
    for n, data in [(0,self.inp), (1,self.tar)]:
      if 'lyrics' in data:
        pair[n] = torch.Tensor(data['lyrics'][begin:end])
      if 'note' in data:
        note = torch.Tensor(data['note'][begin:end])
        tempo = torch.Tensor(data['tempo'][begin:end])
        pair[n] = (note, tempo)

    return tuple(pair)

  def shuffle(self):
    data = list(self.inp.values())+list(self.tar.values())
    data = list(zip(*data))
    random.shuffle(data)
    data = [ list(d) for d in zip(*data) ]
    for k, v in zip(self.inp, data[:len(self.inp)]):
      self.inp[k] = v
    for k, v in zip(self.tar, data[len(self.inp):]):
      self.tar[k] = v

  def __len__(self):
    return (self.size+self.bs-1) // self.bs

  def __repr__(self):
    return yaml.dump({'Dataset': dict(
      data = dict(
        inp = ', '.join(list(self.inp)),
        tar = ', '.join(list(self.tar)),
      ),
      size = self.size,
      batch_size = self.bs,
    )}, default_flow_style=False)

  '''
  def split(self, ratio=0.2):
    n = int(self.size*(1-ratio))
    def div(d, is_f):
      r = {}
      for k, v in d.items():
        r[k] = v[:n] if is_f==0 else v[n:]
      return r
    d1, d2 = [Dataset(div(self.inp,i), div(self.tar,i), self.bs)
        for i in [0, 1]]
    return d1, d2
  '''
    
def loadPath(n=None, lyr_path="data/seg/*.txt", midi_path="data/csv/*.csv"):
  from glob import glob
  import os
  name = lambda x: os.path.splitext(os.path.basename(x))[0]
  '''
  lyr_path, midi_path = glob(lyr_path), glob(midi_path)
  both = set(map(name, lyr_path)) & set(map(name, midi_path))
  lyr_path  = list(filter(lambda n: name(n) in both, lyr_path))
  midi_path = list(filter(lambda n: name(n) in both, midi_path))
  lyr_path.sort(key=os.path.basename)
  midi_path.sort(key=os.path.basename)
  if n==0: n = len(lyr_path)
  '''
  names = set(map(name, glob(lyr_path))) & set(map(name, glob(midi_path)))
  names = sorted(list(names))[:n]
  return [(id, name,
           lyr_path.replace('*', name),
           midi_path.replace('*', name)) for id, name in enumerate(names)]

  '''
  for lyr, midi in list(zip(lyr_path, midi_path))[:n]:
    yield lyr, midi
  '''

def load(filename):
  '''
  from collections import defaultdict
  lyrics, note, tempo = [], [], []
  for lyrics_path, midi_path in loadData(n):
    lyr = l2v.convert(lyrics_path)
    for n, t in m2v.convert(midi_path):
      lyrics.append(lyr)
      note.append(n)
      tempo.append(t)
  '''
  print("Load data from %s" % filename)
  data = pd.read_json(filename, lines=True)

  inp = {'lyrics': data.lyrics.tolist()}
  tar = {'note': data.note.tolist(), 'tempo': data.tempo.tolist()}

  dataset_tr = Dataset(inp, tar, config.translator.batch_size)
  dataset_ae = Dataset(tar, tar, config.autoencoder.batch_size)

  return dataset_ae, dataset_tr


def save(args):
  import json
  paths = loadPath()
  random.seed(301)
  random.shuffle(paths)
  n = len(paths)
  split = args.split
  train, valid = paths[n//split:], paths[:n//split]
  i = 0
  for f, paths in [(args.train, train), (args.valid, valid)]:
    for fid, name, lyr_path, midi_path in paths:
      lyrics = l2v.convert(lyr_path, is_file=True)
      for j, (note, tempo) in enumerate(m2v.convert(midi_path)):
        id = '%d-%d' % (fid, j)
        data = dict(id=id,
                    name=name, 
                    lyrics=lyrics, 
                    note=note, 
                    tempo=tempo)
        print(json.dumps(data, ensure_ascii=False, sort_keys=True), file=f)
      i += 1
      print('{:>5d}/{:>5d}'.format(i, n), end='\r')

if __name__ == '__main__':
  import argparse
  ap = argparse.ArgumentParser()
  ap.add_argument('-train', '-t', 
      type=argparse.FileType('w'), default='data/train.jsonl')
  ap.add_argument('-valid', '-v', 
      type=argparse.FileType('w'), default='data/valid.jsonl')
  ap.add_argument('-split', '-s', type=int, default=4)
  args = ap.parse_args()
  save(args)

