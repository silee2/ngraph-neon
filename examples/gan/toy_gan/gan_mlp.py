# ----------------------------------------------------------------------------
# Copyright 2017 Nervana Systems Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ----------------------------------------------------------------------------
# GAN
# following example code from https://github.com/AYLIEN/gan-intro
# MLP generator and discriminator
# toy example with 1-D Gaussian data distribution


# TODO
#  - discriminator pretraining
#  - optimizer schedule

import numpy as np

import ngraph as ng
import ngraph.transformers as ngt
from ngraph.frontends.neon import Affine, Sequential
from ngraph.frontends.neon import Rectlin, Identity, Tanh, Logistic
from ngraph.frontends.neon import GaussianInit, ConstantInit
from ngraph.frontends.neon import GradientDescentMomentum, Schedule
from ngraph.frontends.neon import ArrayIterator
from ngraph.frontends.neon import make_bound_computation
from ngraph.frontends.neon import NgraphArgparser
from toygan import ToyGAN

deriv_test = False

np.random.seed(42)

parser = NgraphArgparser(description='MLP GAN example from TF code')
parser.add_argument('--combined_updates',
                    action="store_true",
                    help="update variables in both G and D networks when performing D update")
args = parser.parse_args()

# define commonly used layer in this example
def affine_layer(h_dim, activation, name, scope=None):
    return Affine(nout=h_dim,
                  activation=activation,
                  # YL: use ConstantInit for W for now
                  weight_init=ConstantInit(val=1.0),
                  # weight_init=GaussianInit(var=1.0),
                  bias_init=ConstantInit(val=0.0),
                  name=name,
                  scope=scope)

#  model parameters
h_dim = 4  # GAN.mlp_hidden_size
minibatch_discrimination = False  # for this toy example, seems to be better w/o mb discrim?

num_iterations = 1200
batch_size = 12
num_examples = num_iterations*batch_size

if args.combined_updates:
    # use default graph behavior; do not filter variables for optimization by network
    g_scope = None
    d_scope = None
else:
    # correct GAN training; update generator and discriminator separately
    g_scope = 'generator'
    d_scope = 'discriminator'
print "g_scope, d_scope:", g_scope, d_scope

# 1. generator
# use relu instead of softplus (focus on porting infrastucture fundamental to GAN)
# fixed 1200 training iterations: relu retains distribution width but box without peak at mean;
# early stopping at 940 iterations, cost of 0.952, 3.44 looks better
generator_layers = [affine_layer(h_dim, Rectlin(), name='g0', scope=g_scope), 
                    affine_layer(1, Identity(), name='g1', scope=g_scope)]
generator = Sequential(generator_layers)

# 2. discriminator (not implementing minibatch discrimination right now)
# discriminator_layers = []
discriminator_layers = [affine_layer(2 * h_dim, Tanh(), name='d0', scope=d_scope),
                        affine_layer(2 * h_dim, Tanh(), name='d1', scope=d_scope)]
if minibatch_discrimination:
    raise NotImplementedError
else:
    discriminator_layers.append(affine_layer(2 * h_dim, Tanh(), name='d2', scope=d_scope))
discriminator_layers.append(affine_layer(1, Logistic(), name='d3', scope=d_scope))
discriminator = Sequential(discriminator_layers)

# 3. TODO discriminator pre-training - skip for now, more concerned with graph infrastructure changes
# TODO: try taking pre-training out from TF example (get worse result shown in blog animation?)

# 4. optimizer
# TODO: set up exponential decay schedule and other optimizer parameters
def make_optimizer(name=None):
    learning_rate = 0.005 if minibatch_discrimination else 0.03
    schedule = Schedule()
    optimizer = GradientDescentMomentum(learning_rate,
                momentum_coef=0.0,
                stochastic_round=False,
                wdecay=0.0,
                gradient_clip_norm=None,
                gradient_clip_value=None,
                name=name,
                schedule=schedule)
    return optimizer

# 5. dataloader
toy_gan_data = ToyGAN(batch_size, num_iterations)  # use all default parameters, which are the ones from example TF code
train_data = toy_gan_data.load_data()
train_set = ArrayIterator(train_data, batch_size, num_iterations)

# 6. create model (build network graph)

# neon frontend interface:
# inputs dict would created by ArrayIterator make_placeholders method
inputs = train_set.make_placeholders()

# this does not work. haven't specified axes correctly (batch)
# (batch, sample)
#inputs = {'data_sample': ng.placeholder(()),
#	      'noise_sample': ng.placeholder(())}

# generated sample
z = inputs['noise_sample']
G = generator.train_outputs(z)  # generated sample

# discriminator
x = inputs['data_sample']
# *** does this work with ngraph, using discriminator for two outputs?
D1 = discriminator.train_outputs(x)  # discriminator output on real data sample

# copy the discriminator
discriminator_copy = discriminator.copy()

print_layer_variables = False
if print_layer_variables:
    print discriminator.layers[0].linear.W
    print discriminator.layers[0].bias.W
    print discriminator.layers[1].linear.W
    print discriminator.layers[1].bias.W

    print discriminator_copy.layers[0].linear.W
    print discriminator_copy.layers[0].bias.W
    print discriminator_copy.layers[1].linear.W
    print discriminator_copy.layers[1].bias.W

# cast G axes into x
G_t = ng.axes_with_order(G, reversed(G.axes))
G_cast = ng.cast_axes(G_t, x.axes)

# discriminator output on generated sample
D2 = discriminator_copy.train_outputs(G_cast)

if print_layer_variables:
    print discriminator.layers[0].linear.W
    print discriminator.layers[0].bias.W
    print discriminator.layers[1].linear.W
    print discriminator.layers[1].bias.W
    print discriminator_copy.layers[0].linear.W
    print discriminator_copy.layers[0].bias.W
    print discriminator_copy.layers[1].linear.W
    print discriminator_copy.layers[1].bias.W

loss_d = -ng.log(D1) - ng.log(1 - D2)
# loss_d = ng.cross_entropy_binary(D1, D2)
mean_cost_d = ng.mean(loss_d, out_axes=[])
loss_g = -ng.log(D2)
mean_cost_g = ng.mean(loss_g, out_axes=[])

transformer = ngt.make_transformer()

if deriv_test:
    from ngraph.testing.execution import ExecutorFactory
    with ExecutorFactory() as ex:
        wg_shape = generator.layers[0].linear.W.axes.lengths
        wd_shape = discriminator.layers[0].linear.W.axes.lengths
        dg_g = ex.derivative(mean_cost_g, generator.layers[0].linear.W, z)
        dd_d = ex.derivative(mean_cost_d, discriminator.layers[0].linear.W, x, z)
        dd_g = ex.derivative(mean_cost_d, generator.layers[0].linear.W, z)
        dg_d = ex.derivative(mean_cost_g, discriminator.layers[0].linear.W, z)

        np.random.seed(0)
        x_value = np.random.random(x.axes.lengths)
        z_value = np.random.random(z.axes.lengths)
        wg_value = np.random.random(wg_shape)
        wd_value = np.random.random(wd_shape)
        bprop_g = dg_g(wg_value, z_value)
        bprop_d = dd_d(wd_value, x_value, z_value)
        bprop_d_g = dd_g(wg_value, z_value)
        import ipdb; ipdb.set_trace()
        bprop_g_d = dg_d(wd_value, z_value)


optimizer_d = make_optimizer(name='discriminator_optimizer')
optimizer_g = make_optimizer(name='generator_optimizer')
#updates_d = optimizer_d(loss_d)
#updates_g = optimizer_g(loss_g)
updates_d = optimizer_d(loss_d, variable_scope=d_scope)
updates_g = optimizer_g(loss_g, variable_scope=g_scope)

discriminator_train_outputs = {'batch_cost': mean_cost_d,
		 	                   'updates': updates_d}
# generator_train_outputs = {'batch_cost': mean_cost_g,
# 	         	           'updates': updates_g}
generator_train_outputs = {'batch_cost': mean_cost_g}

train_computation_g = make_bound_computation(transformer, generator_train_outputs, inputs['noise_sample'])  # TODO: G inputs just z - does this matter?
train_computation_d = make_bound_computation(transformer, discriminator_train_outputs, inputs)

discriminator_inference_output = discriminator.inference_outputs(x)
generator_inference_output = generator.inference_outputs(z)

discriminator_inference = transformer.computation(discriminator_inference_output, x)  # this syntax feels funny, with x repeated from inference_outputs
generator_inference = transformer.computation(generator_inference_output, z)

# support variable rate training of discriminator and generator
k = 1  # number of discriminator training iterations (in general may be > 1, for example in WGAN paper)

# 7. train loop
# train_set yields data which is a dictionary of named input values ('data_sample' and 'noise_sample')
iter_interval = 100
for mb_idx, data in enumerate(train_set):
    # update discriminator (trained at a different rate than generator, if k > 1)
    for iter_d in range(k):
        # ** if use cross_entropy_binary for loss_d, errors out here, adjoint axes do not match error
        batch_output_d = train_computation_d(data)  # batch_cost and updates for discriminator
    # update generator
    batch_output_g = train_computation_g(data['noise_sample'])
    if mb_idx % iter_interval == 0:
        msg = "Iteration {} complete. Discriminator avg loss: {} Generator avg loss: {}"
        print(msg.format(mb_idx, float(batch_output_d['batch_cost']), float(batch_output_g['batch_cost'])))

# 8. visualize generator results

# this is basically copied from blog TF code
nrange = toy_gan_data.noise_range
num_points = 10000
num_bins = 100
bins = np.linspace(-nrange, nrange, num_bins)

# decision boundary - discriminator output on real data distribution samples
xs = np.linspace(-nrange, nrange, num_points)
db = np.zeros((num_points, 1))
for i in range(num_points // batch_size):
    sl = slice(i*batch_size, (i+1)*batch_size)
    inp = xs[sl].reshape(batch_size, 1)
    db[sl] = discriminator_inference(inp).reshape(batch_size, 1)  # * returned in shape (1, batch_size), tripped over this

# data distribution
d = toy_gan_data.data_samples(num_points, 1)
pd, i_pd = np.histogram(d, bins=bins, density=True)

# generated samples
zs = np.linspace(-nrange, nrange, num_points)
g = np.zeros((num_points, 1))
for i in range(num_points // batch_size):
    sl = slice(i*batch_size, (i+1)*batch_size)
    g[sl] = generator_inference(zs[sl].reshape(batch_size, 1)).reshape(batch_size, 1)
pg, i_pg = np.histogram(g, bins=bins, density=True)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.plot(pd, 'b', label='real data')
    plt.plot(pg, 'g', label='generated data')
    plt.legend(loc='upper left')
    plt.savefig('ng_gan.png')
except ImportError:
    print ("needs matplotlib")

# save off data for plot generation
import h5py
with h5py.File('simple_gan.h5', 'w') as f:
    f.create_dataset('decision_boundary', (len(db), 1), dtype=float)
    f['decision_boundary'][:] = db
    f.create_dataset('data_distribution', (len(pd), ), dtype=float)
    f['data_distribution'][:] = pd
    f.create_dataset('generated_distribution', (len(pg), ), dtype=float)
    f['generated_distribution'][:] = pg
    # distribution histograms indices
    f.create_dataset('data_dist_index', (len(i_pd), ), dtype=float)
    f['data_dist_index'][:] = i_pd
    f.create_dataset('generated_dist_index', (len(i_pg), ), dtype=float)
    f['generated_dist_index'][:] = i_pg
