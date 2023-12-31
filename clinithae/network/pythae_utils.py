import abc

import torch
from pythae.models.base.base_utils import ModelOutput
from pythae.models.nn import BaseDecoder, BaseEncoder
from torch import nn

from clinicadl.utils.network.network import Network
from clinicadl.utils.network.vae.vae_layers import (
    DecoderLayer3D,
    EncoderLayer3D,
    Flatten,
    Unflatten3D,
)


class BasePythae(Network):
    def __init__(
        self,
        input_size,
        first_layer_channels,
        n_conv_encoder,
        feature_size,
        latent_space_size,
        n_conv_decoder,
        last_layer_channels,
        last_layer_conv,
        gpu,
        is_ae=False,
    ):
        super(BasePythae, self).__init__(gpu=gpu)

        # self.model = None

        self.input_size = input_size
        self.latent_space_size = latent_space_size

        encoder_layers, mu_layer, logvar_layer, decoder_layers = build_encoder_decoder(
            input_size=input_size,
            first_layer_channels=first_layer_channels,
            n_conv_encoder=n_conv_encoder,
            feature_size=feature_size,
            latent_space_size=latent_space_size,
            n_conv_decoder=n_conv_decoder,
            last_layer_channels=last_layer_channels,
            last_layer_conv=last_layer_conv,
        )

        if is_ae:
            encoder = Encoder_AE(encoder_layers, mu_layer)
        else:
            encoder = Encoder_VAE(encoder_layers, mu_layer, logvar_layer)
        decoder = Decoder(decoder_layers)

        return encoder, decoder

    @abc.abstractmethod
    def get_model(self, encoder, decoder):
        pass

    @property
    def layers(self):
        return torch.nn.Sequential(self.model.encoder, self.model.decoder)

    def compute_outputs_and_loss(self, input_dict, criterion, use_labels=False):
        # x = input_dict["image"].to(self.device)
        model_outputs = self.forward(input_dict)
        loss_dict = {
            "loss": model_outputs.loss,
        }
        for key in model_outputs.keys():
            if "loss" in key:
                loss_dict[key] = model_outputs[key]
        return model_outputs.recon_x, loss_dict

    # Network specific
    def predict(self, x):
        return self.model.predict(x.data)

    def forward(self, x):
        return self.model.forward(x)

    def transfer_weights(self, state_dict, transfer_class):
        self.model.load_state_dict(state_dict)

    # VAE specific
    # def encode(self, x):
    #     encoder_output = self.encoder(x)
    #     mu, logVar = encoder_output.embedding, encoder_output.log_covariance
    #     z = self.reparameterize(mu, logVar)
    #     return mu, logVar, z

    # def decode(self, z):
    #     z = self.decoder(z)
    #     return z

    # def reparameterize(self, mu, logVar):
    #     #print(logVar.shape)
    #     std = torch.exp(0.5 * logVar)
    #     eps = torch.randn_like(std)
    #     return eps.mul(std).add_(mu)


def build_encoder_decoder(
    input_size=(1, 80, 96, 80),
    first_layer_channels=32,
    n_conv_encoder=3,
    feature_size=0,
    latent_space_size=128,
    n_conv_decoder=3,
    last_layer_channels=32,
    last_layer_conv=False,
):
    input_c = input_size[0]
    input_d = input_size[1]
    input_h = input_size[2]
    input_w = input_size[3]

    # ENCODER
    encoder_layers = []
    # Input Layer
    encoder_layers.append(EncoderLayer3D(input_c, first_layer_channels))

    # Conv Layers
    for i in range(n_conv_encoder - 1):
        encoder_layers.append(
            EncoderLayer3D(
                first_layer_channels * 2**i, first_layer_channels * 2 ** (i + 1)
            )
        )
        # Construct output paddings
    # Compute size of the feature space
    n_pix_encoder = (
        first_layer_channels
        * 2 ** (n_conv_encoder - 1)
        * (input_d // (2**n_conv_encoder))
        * (input_h // (2**n_conv_encoder))
        * (input_w // (2**n_conv_encoder))
    )
    # Flatten
    encoder_layers.append(Flatten())
    # Intermediate feature space
    if feature_size == 0:
        feature_space = n_pix_encoder
    else:
        feature_space = feature_size

        encoder_layers.append(
            nn.Sequential(nn.Linear(n_pix_encoder, feature_space), nn.ReLU())
        )
    encoder = nn.Sequential(*encoder_layers)

    # LATENT SPACE
    mu_layer = nn.Linear(feature_space, latent_space_size)
    var_layer = nn.Linear(feature_space, latent_space_size)

    # DECODER

    # automatically compute padding
    d, h, w = input_d, input_h, input_w
    decoder_output_padding = []
    decoder_output_padding.append([d % 2, h % 2, w % 2])
    d, h, w = d // 2, h // 2, w // 2
    for i in range(n_conv_decoder - 1):
        decoder_output_padding.append([d % 2, h % 2, w % 2])
        d, h, w = d // 2, h // 2, w // 2

    n_pix_decoder = (
        last_layer_channels
        * 2 ** (n_conv_decoder - 1)
        * (input_d // (2**n_conv_decoder))
        * (input_h // (2**n_conv_decoder))
        * (input_w // (2**n_conv_decoder))
    )

    decoder_layers = []
    # Intermediate feature space
    if feature_size == 0:
        decoder_layers.append(
            nn.Sequential(
                nn.Linear(latent_space_size, n_pix_decoder),
                nn.ReLU(),
            )
        )
    else:
        decoder_layers.append(
            nn.Sequential(
                nn.Linear(latent_space_size, feature_size),
                nn.ReLU(),
                nn.Linear(feature_size, n_pix_decoder),
                nn.ReLU(),
            )
        )
    # Unflatten
    decoder_layers.append(
        Unflatten3D(
            last_layer_channels * 2 ** (n_conv_decoder - 1),
            input_d // (2**n_conv_decoder),
            input_h // (2**n_conv_decoder),
            input_w // (2**n_conv_decoder),
        )
    )
    # Decoder layers
    for i in range(n_conv_decoder - 1, 0, -1):
        decoder_layers.append(
            DecoderLayer3D(
                last_layer_channels * 2 ** (i),
                last_layer_channels * 2 ** (i - 1),
                output_padding=decoder_output_padding[i],
            )
        )
    # Output conv layer
    if last_layer_conv:
        last_layer = nn.Sequential(
            DecoderLayer3D(
                last_layer_channels,
                last_layer_channels,
                4,
                stride=2,
                padding=1,
                output_padding=decoder_output_padding[0],
            ),
            nn.Conv3d(last_layer_channels, input_c, 3, stride=1, padding=1),
            nn.Sigmoid(),
        )

    else:
        last_layer = nn.Sequential(
            nn.ConvTranspose3d(
                last_layer_channels,
                input_c,
                4,
                stride=2,
                padding=1,
                output_padding=decoder_output_padding[0],
                bias=False,
            ),
            nn.Sigmoid(),
        )
    decoder_layers.append(last_layer)
    decoder = nn.Sequential(*decoder_layers)
    return encoder, mu_layer, var_layer, decoder


class Encoder_VAE(BaseEncoder):
    def __init__(
        self, encoder_layers, mu_layer, logvar_layer
    ):  # Args is a ModelConfig instance
        BaseEncoder.__init__(self)

        self.layers = encoder_layers
        self.mu_layer = mu_layer
        self.logvar_layer = logvar_layer

    def forward(self, x: torch.Tensor) -> ModelOutput:
        h = self.layers(x)
        mu, logVar = self.mu_layer(h), self.logvar_layer(h)
        output = ModelOutput(
            embedding=mu,  # Set the output from the encoder in a ModelOutput instance
            log_covariance=logVar,
        )
        return output


class Encoder_AE(BaseEncoder):
    def __init__(self, encoder_layers, mu_layer):  # Args is a ModelConfig instance
        BaseEncoder.__init__(self)

        self.layers = encoder_layers
        self.mu_layer = mu_layer

    def forward(self, x: torch.Tensor) -> ModelOutput:
        h = self.layers(x)
        embedding = self.mu_layer(h)
        output = ModelOutput(
            embedding=embedding,  # Set the output from the encoder in a ModelOutput instance
        )
        return output


class Decoder(BaseDecoder):
    def __init__(self, decoder_layers):
        BaseDecoder.__init__(self)

        self.layers = decoder_layers

    def forward(self, x: torch.Tensor) -> ModelOutput:
        out = self.layers(x)
        output = ModelOutput(
            reconstruction=out  # Set the output from the decoder in a ModelOutput instance
        )
        return output
