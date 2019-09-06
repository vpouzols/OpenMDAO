"""Define the DictionaryJacobian class."""
from __future__ import division

import numpy as np
from six.moves import range
from scipy.sparse import csc_matrix

from openmdao.jacobians.jacobian import Jacobian


_full_slice = slice(None)


class DictionaryJacobian(Jacobian):
    """
    No global <Jacobian>; use dictionary of user-supplied sub-Jacobians.

    Attributes
    ----------
    _iter_keys : list of (vname, vname) tuples
        List of tuples of variable names that match subjacs in the this Jacobian.

    """

    def __init__(self, system, **kwargs):
        """
        Initialize all attributes.

        Parameters
        ----------
        system : System
            Parent system to this jacobian.
        **kwargs : dict
            options dictionary.
        """
        super(DictionaryJacobian, self).__init__(system, **kwargs)
        self._iter_keys = {}

    def _iter_abs_keys(self, system, vec_name):
        """
        Iterate over subjacs keyed by absolute names.

        This includes only subjacs that have been set and are part of the current system.

        Parameters
        ----------
        system : System
            System that is updating this jacobian.
        vec_name : str
            The name of the current RHS vector.

        Returns
        -------
        list
            List of keys matching this jacobian for the current system.
        """
        entry = (system.pathname, vec_name)

        if entry not in self._iter_keys:
            subjacs = self._subjacs_info
            keys = []
            for res_name in system._var_relevant_names[vec_name]['output']:
                for type_ in ('output', 'input'):
                    for name in system._var_relevant_names[vec_name][type_]:
                        key = (res_name, name)
                        if key in subjacs:
                            keys.append(key)
                            rows = subjacs[key]['rows']
                            if rows is not None:
                                cols = subjacs[key]['cols']
                                # create a CSC matrix to use for the subjac in apply
                                csc = csc_matrix((np.arange(rows.size, dtype=int), (rows, cols)),
                                                 shape=subjacs[key]['shape'])
                                inds = csc.data
                                if np.all(np.arange(rows.size, dtype=int) == inds):
                                    # no mapping needed
                                    inds = slice(None)
                                else:
                                    # reverse inds to avoid an array copy during apply
                                    inds = np.argsort(inds)
                                # keep track of how data array was modified in conversion
                                # from COO to CSC
                                subjacs[key]['csc_val_map'] = inds
                                subjacs[key]['csc'] = csc_matrix((subjacs[key]['value'],
                                                                 (rows, cols)),
                                                                 shape=subjacs[key]['shape'])

            self._iter_keys[entry] = keys

        return self._iter_keys[entry]

    def _apply(self, system, d_inputs, d_outputs, d_residuals, mode):
        """
        Compute matrix-vector product.

        Parameters
        ----------
        system : System
            System that is updating this jacobian.
        d_inputs : Vector
            inputs linear vector.
        d_outputs : Vector
            outputs linear vector.
        d_residuals : Vector
            residuals linear vector.
        mode : str
            'fwd' or 'rev'.
        """
        # avoid circular import
        from openmdao.core.explicitcomponent import ExplicitComponent

        fwd = mode == 'fwd'
        d_res_names = d_residuals._names
        d_out_names = d_outputs._names
        d_inp_names = d_inputs._names
        rflat = d_residuals._views_flat
        oflat = d_outputs._views_flat
        iflat = d_inputs._views_flat
        np_add_at = np.add.at
        ncol = d_residuals._ncol
        subjacs_info = self._subjacs_info
        is_explicit = isinstance(system, ExplicitComponent)

        with system._unscaled_context(outputs=[d_outputs], residuals=[d_residuals]):
            for abs_key in self._iter_abs_keys(system, d_residuals._name):
                subjac_info = subjacs_info[abs_key]
                if self._randomize:
                    subjac = self._randomize_subjac(subjac_info['value'], abs_key)
                else:
                    subjac = subjac_info['value']
                res_name, other_name = abs_key
                if res_name in d_res_names:
                    rows = subjac_info['rows']
                    if rows is not None:  # sparse list format
                        if other_name in d_out_names:
                            # skip the matvec mult completely for identity subjacs
                            if res_name is other_name and is_explicit:
                                if fwd:
                                    rflat[res_name] -= oflat[other_name]
                                else:
                                    oflat[other_name] -= rflat[res_name]
                            else:
                                cols = subjac_info['cols']
                                csc = subjac_info['csc']
                                inds = subjac_info['csc_val_map']
                                csc.data[inds] = subjac
                                if fwd:
                                    if ncol > 1:
                                        for i in range(ncol):
                                            np_add_at(rflat[res_name][:, i], rows,
                                                      oflat[other_name][:, i][cols] * subjac)
                                    else:
                                        rflat[res_name] += csc.dot(oflat[other_name])
                                else:  # rev
                                    if ncol > 1:
                                        for i in range(ncol):
                                            np_add_at(oflat[other_name][:, i], cols,
                                                      rflat[res_name][:, i][rows] * subjac)
                                    else:
                                        oflat[other_name] += csc.transpose().dot(rflat[res_name])
                        elif other_name in d_inp_names:
                            cols = subjac_info['cols']
                            csc = subjac_info['csc']
                            inds = subjac_info['csc_val_map']
                            csc.data[inds] = subjac
                            if fwd:
                                if ncol > 1:
                                    for i in range(ncol):
                                        np_add_at(rflat[res_name][:, i], rows,
                                                  iflat[other_name][:, i][cols] * subjac)
                                else:
                                    rflat[res_name] += csc.dot(iflat[other_name])
                            else:  # rev
                                if ncol > 1:
                                    for i in range(ncol):
                                        np_add_at(iflat[other_name][:, i], cols,
                                                  rflat[res_name][:, i][rows] * subjac)
                                else:
                                    iflat[other_name] += csc.transpose().dot(rflat[res_name])
                    else:  # ndarray or sparse
                        if other_name in d_out_names:
                            if fwd:
                                rflat[res_name] += subjac.dot(oflat[other_name])
                            else:  # rev
                                oflat[other_name] += subjac.T.dot(rflat[res_name])
                        elif other_name in d_inp_names:
                            if fwd:
                                rflat[res_name] += subjac.dot(iflat[other_name])
                            else:  # rev
                                iflat[other_name] += subjac.T.dot(rflat[res_name])
