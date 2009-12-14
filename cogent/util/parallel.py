#!/usr/bin/env python

import os, logging

__author__ = "Andrew Butterfield"
__copyright__ = "Copyright 2007-2009, The Cogent Project"
__credits__ = ["Andrew Butterfield", "Peter Maxwell", "Gavin Huttley",
                "Matthew Wakefield", "Edward Lang"]
__license__ = "GPL"
__version__ = "1.4.0.dev"
__maintainer__ = "Gavin Huttley"
__email__ = "Gavin Huttley"
__status__ = "Production"

LOG = logging.getLogger('cogent')

# A flag to control if excess CPUs are worth a warning.
inefficiency_forgiven = False

class _FakeCommunicator(object):
    """Looks like a 1-cpu MPI communicator, but isn't"""
    def Get_rank(self):
        return 0
    def Get_size(self):
        return 1
    def Split(self, colour, key=0):
        return (self, self)
    def allreduce(self, value, op=None):
        return value
    def allgather(self, value):
        return [value]
    def bcast(self, obj, source):
        return obj
    def Bcast(self, array, source):
        pass
    def Barrier(self):
        pass

class _FakeMPI(object):
    # required MPI module constants
    SUM = MAX = DOUBLE = 'fake'   

if os.environ.get('DONT_USE_MPI', 0):
    mpi = None
else:
    try:
        from mpi4py import MPI
    except ImportError:
        MPI = None
    else:
        size = MPI.COMM_WORLD.Get_size()
        LOG.info('MPI: %s processors' % size)
        if size == 1:
            MPI = None

if MPI is None:
    LOG.info('Not using MPI')
    def get_processor_name():
        return os.environ.get('HOSTNAME', 'one')
    _ParallelisationStack = [_FakeCommunicator()]
    MPI = _FakeMPI()
else:
    get_processor_name = MPI.Get_processor_name
    _ParallelisationStack = [MPI.COMM_WORLD]

def push(context):
    _ParallelisationStack.append(context)

def pop(context=None):
    context2 = _ParallelisationStack.pop()
    if context is not None:
        assert context2 is context
    return context2

def sync_random(r):
    if _ParallelisationStack[-1].Get_size() > 1:
        state = _ParallelisationStack[-1].bcast(r.getstate(), 0)
        r.setstate(state)

def getCommunicator():
    return _ParallelisationStack[-1]

def getSplitCommunicators(jobs):
    comm = getCommunicator()
    assert jobs > 0
    (size, rank) = (comm.Get_size(), comm.Get_rank())
    group_count = min(jobs, size)
    if group_count == 1:
        (next, sub) = (_FakeCommunicator(), comm)
    elif group_count == size:
        (next, sub) = (comm, _FakeCommunicator())
    else:
        next = comm.Split(rank // group_count, rank)
        sub = comm.Split(rank % group_count, rank)
    return (next, sub)


# These two classes should be one simple generator definition,
# but can't wrap a 'yield' in a 'try ... finally' and I want it to
# be safe for loops with 'break' etc.
class _ParallelIter(object):
    def __init__(self, values, cpus, leftover):
        self.values = values
        self.leftover = leftover
        self.cpus = cpus
        self.i = cpus.Get_rank()
        push(self.leftover)
    
    def next(self):
        self.leftover.Barrier()
        if self.i >= len(self.values):
            raise StopIteration
        result = self.values[self.i]
        self.i += self.cpus.Get_size()
        return result
    
    def __del__(self):
        self.cpus.Barrier()
        pop(self.leftover)
    

class localShareOf(object):
    """For task in localShareOf(tasks): ..."""
    def __init__(self, values):
        self.values = values
        (self.cpus, self.leftover) = getSplitCommunicators(len(values))
    
    def __iter__(self):
        return _ParallelIter(self.values, self.cpus, self.leftover)
    

class ParaRandom:
    """Converts any random number generator with a .random() method
    into an MPI safe parallel random number generator.
    This relies on ParaRNG being passed the correct number of processes and rank.
    Internally ParaRNG assigns a phase for each process so that process n
    will always get the n th random number in the series.
    Without this method most random number generators will generate the same
    series on SMP machines and some MPI clusters.
    Can safely be used on itself to provide ParaRNG in nested parallelism.
    
    Warning: accessing the random number generator passed to this function after
    passing and without resetting the seed could generate duplicate values to
    those previously generated by this class or disrupt the phasing
    
    Arguments:
        o    random_number - a random number generator with a .random() method
             that generates a value between 0.0 and 1.0
        i    num_proc - the number of processes
        i    rank - the current processers rank
    
    """
    def __init__(self, random_number, num_proc = 1, rank = 0 ):
        self._rng = random_number
        self._num_proc = num_proc
        self._rank = rank
        #set the initial position in the random number series
        for i in range(self._rank):
            self._rng.random()
    
    def random(self):
        #get the current random number in the series
        r = self._rng.random()
        #advance by the number of processors to preposition for next call
        for i in range(self._num_proc):
            self._rng.random()
        return r
    
    def seed(self, arg):
        self._rng.seed(arg)
    

output_cpu = getCommunicator().Get_rank() == 0
