"""Bounded design arithmetic and observed uncertainty; no simulations or agents."""
from collections import defaultdict
from functools import lru_cache
import math


def binary_design(cohorts):
    if type(cohorts) is not int or not 1 <= cohorts <= 1000:
        raise ValueError('Design diagnostics require 1..1000 planned cohorts')
    return {'planned_cohorts': cohorts,
        'difference_grid': [i/cohorts for i in range(-cohorts,cohorts+1)] if cohorts <= 100 else None,
        'minimum_two_sided_exact_p': min(1.,2.**(1-cohorts)),
        'test_scope': 'idealized equal-weight independent paired binary outcomes; all pairs discordant at the floor',
        'iid_half_success_perfect_separation_either_direction': 2*(.25**cohorts) or None,
        'iid_half_success_perfect_separation_prespecified_direction': .25**cohorts or None,
        'separation_assumption': 'independent arms and cohorts; each success probability 0.5; not implied by equal marginal rates',
        'empirical_power': None, 'case_minimum_detectable_effect': None}


@lru_cache(maxsize=201)
def _null_binomial_pvalues(n):
    total=2**n
    return tuple(min(1.,2*sum(math.comb(n,j) for j in range(min(k,n-k)+1))/total) for k in range(n+1))


def matched_binary_power(cohorts, favorable_probability, unfavorable_probability, alpha=.05):
    """Exact prospective McNemar power for specified iid pair probabilities."""
    if type(cohorts) is not int or not 1 <= cohorts <= 200:
        raise ValueError('Exact power is bounded to 1..200 independent pairs')
    values=(favorable_probability,unfavorable_probability,alpha)
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise ValueError('Probabilities must be finite numbers')
    discordance=favorable_probability+unfavorable_probability
    if min(favorable_probability,unfavorable_probability)<0 or discordance>1 or not 0<alpha<1:
        raise ValueError('Invalid pair probabilities or alpha')
    if discordance==0:return 0.
    direction=favorable_probability/discordance
    power=0.
    for n in range(1,cohorts+1):
        mass=math.comb(cohorts,n)*discordance**n*(1-discordance)**(cohorts-n)
        conditional=sum(math.comb(n,k)*direction**k*(1-direction)**(n-k)
                        for k,p in enumerate(_null_binomial_pvalues(n)) if p<=alpha)
        power+=mass*conditional
    return min(1.,max(0.,power))


def repeatability(values):
    import statistics
    values=[float(v) for v in values if v is not None]
    if len(values)>100 or not all(math.isfinite(v) for v in values):
        raise ValueError('Repeatability requires at most 100 finite observed repetitions')
    result={'observed_repetitions':len(values),'variance_df':max(0,len(values)-1),
            'mean':statistics.mean(values) if values else None,'sample_variance':None,
            'standard_error_of_conditional_mean':None,'population_repeatability_established':False,
            'reason':'insufficient_repetitions' if len(values)<2 else None}
    if len(values)>=2:
        try:
            variance=statistics.variance(values)
            if not math.isfinite(variance):raise OverflowError
            result.update(sample_variance=variance,standard_error_of_conditional_mean=math.sqrt(variance/len(values)))
        except OverflowError:
            result['reason']='arithmetic_overflow'
    return result


def stratified_mcse(rows):
    """Mean repetitions, then independent cohorts/cases/templates; no gene pooling.

    Shared input clusters that span cases or multiple cohort IDs are rejected
    rather than treated as additional independent Monte Carlo replicates.
    """
    import statistics
    if len(rows)>100_000:raise ValueError('MCSE input exceeds its bound')
    cohorts=defaultdict(list);clusters=defaultdict(set);cohort_clusters=defaultdict(set)
    for row in rows:
        value=row['value']
        if value is None:continue
        if not math.isfinite(value):raise ValueError('MCSE values must be finite')
        key=(row['template_id'],row['case_id'],row['cohort_id'],row['independence_id'])
        cohorts[key].append(float(value));clusters[row['independence_id']].add(key[:3])
        cohort_clusters[key[:3]].add(row['independence_id'])
    result={'monte_carlo_se':None,'independent_cohort_count':len(clusters),
            'scope':'available cohort means, equal cases within templates and equal templates',
            'reason':None,'degenerate_observations':None}
    if any(len(group)>1 for group in clusters.values()) or any(len(group)>1 for group in cohort_clusters.values()):
        result['reason']='shared_independence_cluster_across_cohorts_or_cases';return result
    strata=defaultdict(list)
    for (template,case,_,_),values in cohorts.items():strata[(template,case)].append(statistics.mean(values))
    if not strata or any(len(values)<2 for values in strata.values()):
        result['reason']='fewer_than_two_independent_cohorts_per_available_case';return result
    templates=defaultdict(int)
    for template,_ in strata:templates[template]+=1
    try:
        variance=sum((1/len(templates)/templates[template])**2*statistics.variance(values)/len(values)
                     for (template,_),values in strata.items())
        if not math.isfinite(variance):raise OverflowError
        result.update(monte_carlo_se=math.sqrt(variance),degenerate_observations=variance==0)
    except OverflowError:result['reason']='arithmetic_overflow'
    return result
